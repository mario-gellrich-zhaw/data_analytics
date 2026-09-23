"""The only door to the web for scraper code the agents write themselves.

Agent-written scrapers (see scraper_tool.py) run in a separate Python
process and may import nothing network-related except this module — the
politeness rules live here, in code, not in a prompt the model could
ignore:

- only a short allowlist of domains can be fetched at all
- robots.txt is really checked first (stdlib semantics: 401/403 on
  robots.txt means "everything disallowed"; unreadable means "don't go")
- a randomized 2–5 s pause before every request after the first
- a hard cap on requests per scraper run
- the first 403/429 (or a Cloudflare bot challenge) blocks that host for
  the rest of the run — no retries, no workarounds
- an honest User-Agent, no browser impersonation

Every request (allowed or not) is appended to a JSON-lines log the app
reads back afterwards, so the UI can show exactly what happened — plus
the real structure (JSON keys / HTML title) of the first page fetched from
each site, so the agent can fix its parsing against real data.

Usage from agent code:

    import scraper_kit
    data = scraper_kit.polite_get("https://flatfox.ch/api/v1/public-listing/",
                                  params={"limit": 100, "offset": 0}).json()
    scraper_kit.save_rows([{...}, ...])
"""

import csv
import json
import os
import random
import time
from dataclasses import dataclass, field
from typing import NoReturn
from urllib.parse import urlencode, urlparse
from urllib.robotparser import RobotFileParser

import requests

USER_AGENT = "ZHAW-DataAnalytics-teaching-demo/1.0 (non-commercial; low request rate)"
ALLOWED_DOMAINS = ("flatfox.ch", "immoscout24.ch", "homegate.ch")
REQUEST_TIMEOUT_SECONDS = 10
MIN_DELAY_SECONDS, MAX_DELAY_SECONDS = 2.0, 5.0

MAX_REQUESTS = int(os.environ.get("SCRAPER_KIT_MAX_REQUESTS", "15"))
MAX_ROWS = int(os.environ.get("SCRAPER_KIT_MAX_ROWS", "150"))
LOG_PATH = os.environ.get("SCRAPER_KIT_LOG", "requests_log.jsonl")
OUT_PATH = os.environ.get("SCRAPER_KIT_OUT", "scraped_listings.csv")

# The fixed output schema — save_rows() rejects any other key so the
# Data Engineer's later steps always see the same, predictable columns.
FIELDS = (
    "source",
    "listing_id",
    "title",
    "rent_gross_chf",
    "rent_net_chf",
    "rent_charges_chf",
    "rooms",
    "living_space_m2",
    "floor",
    "year_built",
    "street",
    "zip",
    "city",
    "lat",
    "lon",
    "url",
)


class ScrapeBlocked(Exception):
    """Raised when a request is refused — by the site (403/429, bot
    challenge, other non-200), by robots.txt, or by this kit's own rules
    (domain not allowed, request budget used up). The message says which.
    Treat it as a real "no": don't try to get around it."""


@dataclass(frozen=True)
class Page:
    """One successfully fetched page — plain data only (no live
    connection object), so agent code can't send requests around the
    rules through it."""

    url: str
    status_code: int
    text: str
    headers: dict = field(default_factory=dict)

    def json(self):
        return json.loads(self.text)


_state = {"requests_made": 0, "last_request_at": None}
_robots: dict[str, RobotFileParser | None] = {}  # host -> parser (None = unreadable)
_blocked_hosts: dict[str, str] = {}  # host -> reason
_shapes_logged: set[str] = set()  # hosts whose first page's structure is already logged
_rows: list[dict] = []
_row_keys: set[tuple] = set()


def _log(entry: dict) -> None:
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _host_allowed(host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in ALLOWED_DOMAINS)


def _pause() -> None:
    if _state["last_request_at"] is not None:
        wait = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
        elapsed = time.monotonic() - _state["last_request_at"]
        if elapsed < wait:
            time.sleep(wait - elapsed)
    _state["last_request_at"] = time.monotonic()


def _robots_for(scheme: str, host: str) -> RobotFileParser | None:
    if host in _robots:
        return _robots[host]
    robots_url = f"{scheme}://{host}/robots.txt"
    parser = RobotFileParser(robots_url)
    _pause()
    try:
        response = requests.get(
            robots_url, headers={"User-Agent": USER_AGENT}, timeout=REQUEST_TIMEOUT_SECONDS
        )
        status = response.status_code
        if status in (401, 403):
            parser.disallow_all = True
        elif 400 <= status < 500:
            parser.allow_all = True
        elif status == 200:
            parser.parse(response.text.splitlines())
        else:
            parser = None
        _log({"url": robots_url, "kind": "robots.txt", "status": status})
    except requests.RequestException as exc:
        parser = None
        _log({"url": robots_url, "kind": "robots.txt", "status": None, "error": str(exc)})
    _robots[host] = parser
    return parser


def _refuse(url: str, reason: str, status=None, host: str | None = None) -> NoReturn:
    if host:
        _blocked_hosts[host] = reason
    _log({"url": url, "kind": "page", "status": status, "blocked": reason})
    raise ScrapeBlocked(reason)


def _looks_like_bot_challenge(response: requests.Response) -> bool:
    if response.headers.get("cf-mitigated", "").lower() == "challenge":
        return True
    head = response.text[:2000].lower() if "html" in response.headers.get("Content-Type", "") else ""
    return "<title>just a moment" in head


def _shape_of(response: requests.Response) -> dict:
    """The real structure of a response — JSON keys (top level and the
    first item of the first list), or an HTML page's <title> — so the agent
    can write its parsing code against what's actually there."""
    try:
        data = json.loads(response.text)
    except ValueError:
        text = response.text
        start = text.lower().find("<title>")
        end = text.lower().find("</title>", start)
        title = text[start + 7 : end].strip() if 0 <= start < end else ""
        return {"type": "html", "title": title[:120], "chars": len(text)}
    if not isinstance(data, dict):
        return {"type": "json", "top_level": type(data).__name__}
    shape: dict = {"type": "json", "top_level_keys": list(data)[:40]}
    for key, value in data.items():
        if isinstance(value, list) and value and isinstance(value[0], dict):
            shape[f"keys_of_first_item_in_{key}"] = list(value[0])[:80]
            break
    return shape


def polite_get(url: str, params: dict | None = None) -> Page:
    """Fetch one URL politely. Returns a `Page` (use `.json()` or `.text`)
    on HTTP 200; raises `ScrapeBlocked` otherwise."""
    full_url = f"{url}{'&' if '?' in url else '?'}{urlencode(params)}" if params else url
    parsed = urlparse(full_url)
    host = parsed.hostname or ""

    if parsed.scheme not in ("http", "https") or not _host_allowed(host):
        _refuse(full_url, f"domain not allowed ({host}); allowed: {', '.join(ALLOWED_DOMAINS)}")
    if host in _blocked_hosts:
        _refuse(full_url, f"{host} already blocked this run ({_blocked_hosts[host]}) — not retrying")
    if _state["requests_made"] >= MAX_REQUESTS:
        _refuse(full_url, f"request budget used up ({MAX_REQUESTS} requests per run)")

    robots = _robots_for(parsed.scheme, host)
    if robots is None:
        _refuse(full_url, "robots.txt could not be read — not continuing", host=host)
    if not robots.can_fetch(USER_AGENT, full_url):
        _refuse(full_url, "robots.txt disallows this URL", host=host)

    _pause()
    _state["requests_made"] += 1
    started = time.monotonic()
    try:
        response = requests.get(
            full_url,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "de-CH,de;q=0.9,en;q=0.8"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        _refuse(full_url, f"request failed ({exc})", host=host)
    elapsed_ms = round((time.monotonic() - started) * 1000)

    final_host = urlparse(response.url).hostname or ""
    if not _host_allowed(final_host):
        _refuse(full_url, f"redirected off the allowlist (to {final_host}) — not following",
                status=response.status_code, host=host)
    if _looks_like_bot_challenge(response):
        _refuse(full_url,
                f"Cloudflare bot challenge (HTTP {response.status_code}) — site blocks automated access",
                status=response.status_code, host=host)
    if response.status_code in (403, 429):
        reason = "rate-limited" if response.status_code == 429 else "forbidden"
        _refuse(full_url, f"HTTP {response.status_code} {reason} — stopping for this site",
                status=response.status_code, host=host)
    if response.status_code != 200:
        _refuse(full_url, f"HTTP {response.status_code} — stopping for this site",
                status=response.status_code, host=host)

    entry = {
        "url": full_url,
        "kind": "page",
        "status": response.status_code,
        "elapsed_ms": elapsed_ms,
        "bytes": len(response.content),
    }
    if host not in _shapes_logged:
        _shapes_logged.add(host)
        entry["shape"] = _shape_of(response)
    _log(entry)
    return Page(
        url=response.url,
        status_code=response.status_code,
        text=response.text,
        headers=dict(response.headers),
    )


def save_rows(rows: list[dict]) -> int:
    """Add listing rows (dicts using only keys from FIELDS; missing keys
    become empty) and rewrite the output CSV. Duplicates (same source +
    listing_id) are skipped, and at most MAX_ROWS rows are kept in total.
    Returns how many rows are saved so far."""
    for row in rows:
        unknown = set(row) - set(FIELDS)
        if unknown:
            raise ValueError(
                f"unknown column(s) {sorted(unknown)} — allowed: {', '.join(FIELDS)}"
            )
        if len(_rows) >= MAX_ROWS:
            break
        key = (row.get("source"), row.get("listing_id"))
        if key[1] is not None and key in _row_keys:
            continue
        _row_keys.add(key)
        _rows.append({field: row.get(field) for field in FIELDS})

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(_rows)
    return len(_rows)

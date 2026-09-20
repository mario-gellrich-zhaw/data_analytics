"""Live "action" tools the Data Source Expert agent can call.

No local fallback dataset, nothing faked, and no pre-decided choices: these
tools report real results and hand real options back to the agent, which
decides what to do with them (which site to try, which dataset/resource to
download, when to re-search). Five real, live tools:

1. `attempt_scrape` really tries to fetch listings from one Swiss rental
   platform (one respectful request, no retries/hammering). Expected to
   fail — these platforms actively guard against automated access — which
   is the point: it's what the agents debate.
2. `search_open_data` queries opendata.swiss's genuinely public CKAN API
   for real Swiss housing/rental datasets and returns each candidate's
   real downloadable resources (format + URL) — the agent picks which
   dataset/resource to use, nothing is pre-selected here.
3. `download_dataset` really downloads whichever resource URL the agent
   chose.
4. `analyze_data` loads the downloaded file with pandas and computes real
   structure/quality stats: row count, duplicate rows, missing values.
5. `preview_data` really reads the first N rows of the downloaded file, for
   when an agent asks to actually see the data.
"""

from pathlib import Path

import pandas as pd
import requests

REQUEST_TIMEOUT_SECONDS = 8
DOWNLOAD_TIMEOUT_SECONDS = 25
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

SCRAPE_TARGETS = {
    "immoscout24.ch": "https://www.immoscout24.ch/de/immobilien/mieten/kanton-zuerich",
    "homegate.ch": "https://www.homegate.ch/mieten/immobilien/kanton-zuerich/trefferliste",
    "comparis.ch": "https://www.comparis.ch/immobilien/result/mietobjekte",
    "tutti.ch": "https://www.tutti.ch/de/li/ganze-schweiz/mieten/immobilien",
}

OPENDATA_SEARCH_URL = "https://opendata.swiss/api/3/action/package_search"
TABULAR_FORMATS = {"CSV", "XLSX", "XLS", "JSON"}


def _locale_text(value) -> str:
    """opendata.swiss often returns text as {"de": ..., "en": ..., ...}
    instead of a plain string — pick a sensible language, falling back to
    whatever is there. Passing a plain string through is a no-op."""
    if isinstance(value, dict):
        return value.get("de") or value.get("en") or next((v for v in value.values() if v), "")
    return value or ""


def attempt_scrape(site: str = "immoscout24.ch", on_progress=None) -> dict:
    """Make one real, respectful GET request to one rental platform.

    Only one attempt — this site has already said, via robots.txt and its
    terms of service, that it doesn't want automated traffic, so this
    doesn't retry or hammer it. The goal is to see, live, what actually
    happens when you try.
    """
    def report(stage: str):
        if on_progress:
            on_progress(stage)

    url = SCRAPE_TARGETS.get(site, SCRAPE_TARGETS["immoscout24.ch"])
    report(f"Trying a live request to {site} ...")
    try:
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        blocked = response.status_code != 200
        report(f"{site}: {'blocked' if blocked else 'reachable'} (HTTP {response.status_code}).")
        return {"site": site, "url": url, "status_code": response.status_code, "blocked": blocked}
    except requests.RequestException as exc:
        report(f"{site}: request failed ({exc}).")
        return {"site": site, "url": url, "status_code": None, "blocked": True, "error": str(exc)}


def search_open_data(query: str = "wohnung miete", on_progress=None) -> dict:
    """Query opendata.swiss's real, public CKAN catalog for relevant datasets.

    Returns each candidate dataset's real downloadable tabular resources
    (format + URL) — nothing is pre-picked; the agent decides which one
    (if any) to actually download based on the titles/organizations shown.
    """
    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Querying opendata.swiss for '{query}' ...")
    response = requests.get(
        OPENDATA_SEARCH_URL,
        params={"q": query, "rows": 8},
        headers=BROWSER_HEADERS,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    payload = response.json()["result"]

    datasets = []
    for pkg in payload["results"]:
        title = _locale_text(pkg.get("display_name")) or _locale_text(pkg.get("title")) or pkg["name"]
        org = pkg.get("organization") or {}
        organization = _locale_text(org.get("title")) or org.get("name", "")
        resources = [
            {"format": (res.get("format") or "").upper(), "url": res.get("url")}
            for res in pkg.get("resources", [])
            if (res.get("format") or "").upper() in TABULAR_FORMATS and res.get("url")
        ]
        datasets.append(
            {
                "title": title,
                "organization": organization,
                "url": f"https://opendata.swiss/de/dataset/{pkg['name']}",
                "resources": resources,
            }
        )

    top5 = datasets[:5]
    report(f"Found {payload['count']} real open datasets on opendata.swiss.")
    return {"query": query, "total_found": payload["count"], "datasets": top5}


def download_dataset(resource_url: str, resource_format: str = "CSV", out_path: str = "downloaded_dataset.csv", on_progress=None) -> dict:
    """Really download the dataset resource the agent chose."""
    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Downloading real data from {resource_url} ...")
    try:
        response = requests.get(resource_url, headers=BROWSER_HEADERS, timeout=DOWNLOAD_TIMEOUT_SECONDS)
        response.raise_for_status()

        # Sanity check: a resource *labeled* CSV/XLSX/JSON can still resolve
        # to an HTML page (redirect, error page, cookie wall). Catch that
        # honestly here rather than silently handing pandas garbage to parse.
        content_type = response.headers.get("Content-Type", "").lower()
        looks_like_html = "text/html" in content_type or response.content.lstrip()[:15].lower().startswith(b"<!doctype html") or response.content.lstrip()[:5].lower().startswith(b"<html")
        if resource_format.upper() != "JSON" and looks_like_html:
            report("Download returned an HTML page, not the real data file.")
            return {"success": False, "error": "The resource URL returned an HTML page instead of the real data file.", "path": out_path}

        Path(out_path).write_bytes(response.content)
        size = len(response.content)
        report(f"Downloaded {size:,} bytes to {Path(out_path).name}.")
        return {"success": True, "bytes": size, "path": out_path, "format": resource_format.upper()}
    except requests.RequestException as exc:
        report(f"Download failed: {exc}")
        return {"success": False, "error": str(exc), "path": out_path}


def analyze_data(path: str = "downloaded_dataset.csv", data_format: str = "CSV", on_progress=None) -> dict:
    """Load the real downloaded file and compute real structure/quality stats."""
    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Loading {Path(path).name} for analysis ...")

    fmt = data_format.upper()
    if fmt in {"XLSX", "XLS"}:
        df = pd.read_excel(path)
    elif fmt == "JSON":
        df = pd.read_json(path)
    else:
        df = pd.read_csv(path, sep=None, engine="python", on_bad_lines="skip", encoding="utf-8-sig")

    n_rows, n_cols = df.shape
    missing = df.isna().sum()
    missing_values = {str(col): int(n) for col, n in missing.items() if n > 0}
    duplicate_rows = int(df.duplicated().sum())

    report(
        f"{n_rows:,} rows, {n_cols} columns, {duplicate_rows} duplicate rows, "
        f"{len(missing_values)} columns with missing values."
    )
    return {
        "n_rows": n_rows,
        "n_columns": n_cols,
        "columns": [str(c) for c in df.columns],
        "duplicate_rows": duplicate_rows,
        "missing_values": missing_values,
    }


def preview_data(path: str = "downloaded_dataset.csv", data_format: str = "CSV", n: int = 10, on_progress=None) -> dict:
    """Really read the first n rows of the downloaded file."""
    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Reading the first {n} rows of {Path(path).name} ...")

    fmt = data_format.upper()
    if fmt in {"XLSX", "XLS"}:
        df = pd.read_excel(path, nrows=n)
    elif fmt == "JSON":
        df = pd.read_json(path).head(n)
    else:
        df = pd.read_csv(path, sep=None, engine="python", on_bad_lines="skip", encoding="utf-8-sig", nrows=n)

    columns = [str(c) for c in df.columns]
    rows = df.astype(object).where(df.notna(), None).values.tolist()

    report(f"Got the first {len(rows)} rows.")
    return {"columns": columns, "rows": rows}


ATTEMPT_SCRAPE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "attempt_scrape",
        "description": (
            "Make one real, live request to a Swiss rental platform to "
            "actually test, live, whether scraping it is possible — don't "
            "just speculate about it. Call this for one platform at a time; "
            "see the real result before deciding whether to try another."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "site": {
                    "type": "string",
                    "enum": list(SCRAPE_TARGETS),
                    "description": "Which platform to test right now.",
                }
            },
            "required": ["site"],
        },
    },
}

SEARCH_OPEN_DATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "search_open_data",
        "description": (
            "Query opendata.swiss's public open-data catalog for real Swiss "
            "housing/rental datasets — the legal alternative once scraping "
            "turns out to be blocked. Returns real candidate datasets, each "
            "with a short resource id (e.g. 'r0') to pass to download_dataset "
            "— you choose which one, if any, looks worth downloading."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search terms, e.g. 'mietpreise'."}
            },
            "required": ["query"],
        },
    },
}

DOWNLOAD_DATASET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "download_dataset",
        "description": (
            "Really download a specific dataset resource you chose from "
            "search_open_data's results, by its short id (e.g. 'r2') — use "
            "the id exactly as shown, don't type out a URL yourself."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "resource_id": {"type": "string", "description": "The resource's id, exactly as returned by search_open_data (e.g. 'r0')."},
            },
            "required": ["resource_id"],
        },
    },
}

ANALYZE_DATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "analyze_data",
        "description": (
            "Load the just-downloaded real data file and compute real structure/"
            "quality stats: row count, column count, duplicate rows, missing "
            "values per column, and the real column names (check these for "
            "whether the data actually has what you need, e.g. a price field)."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

PREVIEW_DATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "preview_data",
        "description": "Really read and return the first N rows of the downloaded data file, when asked to show what the data actually looks like.",
        "parameters": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "How many rows to show, default 10."}
            },
            "required": [],
        },
    },
}

"""Live "action" tools the Data Analyst and Data Engineer agents can call.

No local fallback dataset, nothing faked, and no pre-decided choices: these
tools report real results and hand real options back to whichever agent
called them. Split by who actually owns the work in the process model:

Data Analyst — Collecting data:
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

Data Engineer — Preparing & storing data (no analysis/interpretation here,
just structure/quality checks and real cleaning/storage — that's the point
of this whole role split):
4. `preview_data` really reads the first N rows of the downloaded file.
5. `profile_data` loads the file and computes real structure/quality stats
   (row count, duplicate rows, missing values) — profiling *for cleaning*,
   not data analysis.
6. `clean_data` really drops duplicate rows and/or rows missing key
   columns, writing a real cleaned file.
7. `store_to_database` really writes the cleaned file into a real local
   SQLite database.
8. `run_sql_query` really runs a real (read-only) SQL query against that
   database and returns real rows.

All three agents:
9. `make_sketch` hands through a diagram the agent authored itself (plain
   ASCII or Graphviz DOT source) if it finds a sketch useful — no
   computation, just structured enough for the UI to render it distinctly.
"""

import re
import sqlite3
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

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


def _read_table(path: str, data_format: str, **kwargs) -> pd.DataFrame:
    fmt = data_format.upper()
    if fmt in {"XLSX", "XLS"}:
        return pd.read_excel(path, **kwargs)
    if fmt == "JSON":
        df = pd.read_json(path)
        return df.head(kwargs["nrows"]) if "nrows" in kwargs else df
    return pd.read_csv(
        path, sep=None, engine="python", on_bad_lines="skip", encoding="utf-8-sig", **kwargs
    )


# --- Data Analyst: Collecting data -----------------------------------------

# Response headers worth surfacing when they're present — a mix of ordinary
# diagnostic headers and the ones bot-detection/CDN layers (Cloudflare,
# Akamai, etc.) tend to add, since those are often the real explanation for
# a block.
INTERESTING_RESPONSE_HEADERS = (
    "Content-Type",
    "Server",
    "Content-Length",
    "Location",
    "Retry-After",
    "Via",
    "X-Cache",
    "CF-RAY",
    "CF-Mitigated",
    "cf-chl-bypass",
    "X-Akamai-Transformed",
    "X-Robots-Tag",
)


def _wildcard_disallow_rules(robots_txt: str) -> list[str]:
    """Plain-text Disallow paths under the `User-agent: *` block, for a
    human-readable summary — the real allow/deny verdict below comes from
    RobotFileParser, not this."""
    rules: list[str] = []
    applies = False
    for raw_line in robots_txt.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.lower().startswith("user-agent:"):
            applies = line.split(":", 1)[1].strip() == "*"
            continue
        if applies and line.lower().startswith("disallow:"):
            path = line.split(":", 1)[1].strip()
            if path:
                rules.append(path)
    return rules


def _check_robots_txt(url: str, on_progress=None) -> dict:
    """Really fetch and parse the target site's robots.txt to see, live,
    whether it says our user agent may fetch this exact URL — the actual
    signal `attempt_scrape` is respecting, not just a mention in a comment."""

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    report(f"Checking {robots_url} ...")
    try:
        response = requests.get(robots_url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
    except requests.RequestException as exc:
        report(f"robots.txt check failed ({exc}).")
        return {"url": robots_url, "found": False, "status_code": None, "allowed": None, "error": str(exc)}

    if response.status_code != 200:
        report(f"robots.txt: HTTP {response.status_code} (treating as no restrictions declared).")
        return {"url": robots_url, "found": False, "status_code": response.status_code, "allowed": None}

    parser = RobotFileParser()
    parser.parse(response.text.splitlines())
    allowed = parser.can_fetch(BROWSER_HEADERS["User-Agent"], url)
    disallow_rules = _wildcard_disallow_rules(response.text)
    report(
        f"robots.txt found: {'disallows' if not allowed else 'allows'} fetching this URL "
        f"for our user agent ({len(disallow_rules)} 'Disallow' rule(s) under 'User-agent: *')."
    )
    return {
        "url": robots_url,
        "found": True,
        "status_code": response.status_code,
        "allowed": allowed,
        "disallow_rules_sample": disallow_rules[:8],
    }


def attempt_scrape(site: str = "immoscout24.ch", on_progress=None) -> dict:
    """Make one real, respectful GET request to one rental platform, after
    first really checking its robots.txt.

    Only one attempt — this site has already said, via robots.txt and its
    terms of service, that it doesn't want automated traffic, so this
    doesn't retry or hammer it. The goal is to see, live, what actually
    happens when you try, and to capture enough real detail (robots.txt
    verdict, request sent, response status/headers/timing) to explain why.
    """

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    url = SCRAPE_TARGETS.get(site, SCRAPE_TARGETS["immoscout24.ch"])
    robots_txt = _check_robots_txt(url, on_progress=on_progress)

    request_info = {
        "method": "GET",
        "url": url,
        "headers": dict(BROWSER_HEADERS),
        "timeout_seconds": REQUEST_TIMEOUT_SECONDS,
    }
    report(f"GET {url} ...")
    try:
        started = time.monotonic()
        response = requests.get(url, headers=BROWSER_HEADERS, timeout=REQUEST_TIMEOUT_SECONDS)
        elapsed_ms = round((time.monotonic() - started) * 1000)
        blocked = response.status_code != 200
        response_info = {
            "status_code": response.status_code,
            "reason": response.reason,
            "elapsed_ms": elapsed_ms,
            "final_url": response.url,
            "redirected": response.url != url,
            "content_bytes": len(response.content),
            "headers": {
                name: response.headers[name]
                for name in INTERESTING_RESPONSE_HEADERS
                if name in response.headers
            },
        }
        report(
            f"{site}: {'blocked' if blocked else 'reachable'} "
            f"(HTTP {response.status_code} {response.reason}, {elapsed_ms} ms)."
        )
        return {
            "site": site,
            "url": url,
            "status_code": response.status_code,
            "blocked": blocked,
            "robots_txt": robots_txt,
            "request": request_info,
            "response": response_info,
        }
    except requests.RequestException as exc:
        report(f"{site}: request failed ({exc}).")
        return {
            "site": site,
            "url": url,
            "status_code": None,
            "blocked": True,
            "error": str(exc),
            "robots_txt": robots_txt,
            "request": request_info,
        }


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
    try:
        response = requests.get(
            OPENDATA_SEARCH_URL,
            params={"q": query, "rows": 8},
            headers=BROWSER_HEADERS,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()["result"]
    except requests.RequestException as exc:
        # A real network hiccup (timeout, connection error, HTTP error from
        # opendata.swiss) should read as "this attempt failed, try
        # something else" — same as attempt_scrape/download_dataset — not
        # crash the entire run.
        report(f"opendata.swiss query failed ({exc}).")
        return {"query": query, "total_found": 0, "datasets": [], "error": str(exc)}

    datasets = []
    for pkg in payload["results"]:
        title = (
            _locale_text(pkg.get("display_name")) or _locale_text(pkg.get("title")) or pkg["name"]
        )
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


def download_dataset(
    resource_url: str,
    resource_format: str = "CSV",
    out_path: str = "downloaded_dataset.csv",
    on_progress=None,
) -> dict:
    """Really download the dataset resource the agent chose."""

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Downloading real data from {resource_url} ...")
    try:
        response = requests.get(
            resource_url, headers=BROWSER_HEADERS, timeout=DOWNLOAD_TIMEOUT_SECONDS
        )
        response.raise_for_status()

        # Sanity check: a resource *labeled* CSV/XLSX/JSON can still resolve
        # to an HTML page (redirect, error page, cookie wall). Catch that
        # honestly here rather than silently handing pandas garbage to parse.
        content_type = response.headers.get("Content-Type", "").lower()
        looks_like_html = (
            "text/html" in content_type
            or response.content.lstrip()[:15].lower().startswith(b"<!doctype html")
            or response.content.lstrip()[:5].lower().startswith(b"<html")
        )
        if resource_format.upper() != "JSON" and looks_like_html:
            report("Download returned an HTML page, not the real data file.")
            return {
                "success": False,
                "error": "The resource URL returned an HTML page instead of the real data file.",
                "path": out_path,
            }

        Path(out_path).write_bytes(response.content)
        size = len(response.content)
        report(f"Downloaded {size:,} bytes to {Path(out_path).name}.")
        return {"success": True, "bytes": size, "path": out_path, "format": resource_format.upper()}
    except requests.RequestException as exc:
        report(f"Download failed: {exc}")
        return {"success": False, "error": str(exc), "path": out_path}


def discard_dataset(
    path: str = "downloaded_dataset.csv", reason: str = "", on_progress=None
) -> dict:
    """Really delete a downloaded file that turned out to be unsuitable
    (aggregated, wrong topic, or otherwise unusable) so it can't be mistaken
    for real data later — use before searching for a replacement."""

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    if not path:
        return {"success": True, "deleted": False, "path": path, "reason": reason}
    p = Path(path)
    if p.is_file():
        p.unlink()
        report(f"Deleted {p.name}" + (f" — {reason}" if reason else ""))
        return {"success": True, "deleted": True, "path": path, "reason": reason}
    report(f"Nothing to delete at {p.name}.")
    return {"success": True, "deleted": False, "path": path, "reason": reason}


# --- Data Engineer: Preparing & storing data --------------------------------


def preview_data(
    path: str = "downloaded_dataset.csv", data_format: str = "CSV", n: int = 10, on_progress=None
) -> dict:
    """Really read the first n rows of the file."""

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Reading the first {n} rows of {Path(path).name} ...")
    try:
        df = _read_table(path, data_format, nrows=n)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # A genuinely unreadable file (wrong format guess, corrupt/garbled
        # content) should read as "that failed, try something else" — not
        # crash the entire run. pandas/openpyxl can raise many different
        # exception types depending on what's actually wrong with a file
        # picked at runtime, so this boundary catches broadly on purpose.
        report(f"Couldn't read {Path(path).name} ({exc}).")
        return {"error": str(exc), "columns": [], "rows": []}

    columns = [str(c) for c in df.columns]
    rows = df.astype(object).where(df.notna(), None).values.tolist()

    report(f"Got the first {len(rows)} rows.")
    return {"columns": columns, "rows": rows}


def profile_data(
    path: str = "downloaded_dataset.csv", data_format: str = "CSV", on_progress=None
) -> dict:
    """Load the real file and compute real structure/quality stats — profiling
    to decide what needs cleaning, not data analysis."""

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Profiling {Path(path).name} ...")
    try:
        df = _read_table(path, data_format)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # See preview_data's matching except: same boundary, same reason.
        report(f"Couldn't read {Path(path).name} ({exc}).")
        return {"error": str(exc)}

    n_rows, n_cols = df.shape
    missing = df.isna().sum()
    missing_values = {str(col): int(n) for col, n in missing.items() if n > 0}
    duplicate_rows = int(df.duplicated().sum())
    dtypes = {str(col): str(dtype) for col, dtype in df.dtypes.items()}

    report(
        f"{n_rows:,} rows, {n_cols} columns, {duplicate_rows} duplicate rows, "
        f"{len(missing_values)} columns with missing values."
    )
    return {
        "n_rows": n_rows,
        "n_columns": n_cols,
        "columns": [str(c) for c in df.columns],
        "dtypes": dtypes,
        "duplicate_rows": duplicate_rows,
        "missing_values": missing_values,
    }


def clean_data(
    source_path: str = "downloaded_dataset.csv",
    data_format: str = "CSV",
    out_path: str = "cleaned_dataset.csv",
    drop_duplicates: bool = True,
    drop_missing_in: list | None = None,
    on_progress=None,
) -> dict:
    """Really clean the downloaded file: drop exact duplicate rows and/or
    rows missing values in agent-named key columns. Writes a real new file."""

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Cleaning {Path(source_path).name} ...")
    try:
        df = _read_table(source_path, data_format)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # See preview_data's matching except: same boundary, same reason.
        report(f"Couldn't read {Path(source_path).name} ({exc}).")
        return {"error": str(exc)}
    n_before = len(df)

    if drop_duplicates:
        df = df.drop_duplicates()
    dropped_duplicates = n_before - len(df)

    dropped_missing = 0
    if drop_missing_in:
        valid_cols = [c for c in drop_missing_in if c in df.columns]
        n_before_missing = len(df)
        if valid_cols:
            df = df.dropna(subset=valid_cols)
        dropped_missing = n_before_missing - len(df)

    df.to_csv(out_path, index=False)
    report(
        f"Cleaned: {n_before:,} → {len(df):,} rows "
        f"({dropped_duplicates:,} duplicates, {dropped_missing:,} missing-value rows dropped)."
    )
    return {
        "rows_before": n_before,
        "rows_after": len(df),
        "dropped_duplicates": dropped_duplicates,
        "dropped_missing": dropped_missing,
        "path": out_path,
    }


def store_to_database(
    source_path: str = "cleaned_dataset.csv",
    db_path: str = "rental_data.db",
    table_name: str = "apartments",
    on_progress=None,
) -> dict:
    """Really write the cleaned file into a real local SQLite database."""

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Storing {Path(source_path).name} into {Path(db_path).name} (table '{table_name}') ...")
    try:
        df = pd.read_csv(source_path)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        # See preview_data's matching except: same boundary, same reason.
        report(f"Couldn't read {Path(source_path).name} ({exc}).")
        return {"error": str(exc)}

    safe_table = re.sub(r"[^A-Za-z0-9_]", "_", table_name) or "apartments"
    con = sqlite3.connect(db_path)
    try:
        try:
            df.to_sql(safe_table, con, if_exists="replace", index=False)
            con.commit()
        except sqlite3.Error as exc:
            report(f"Storing failed ({exc}).")
            return {"error": str(exc)}
    finally:
        con.close()

    db_bytes = Path(db_path).stat().st_size
    report(f"Stored {len(df):,} rows in table '{safe_table}' ({db_bytes:,} bytes).")
    return {
        "db_path": db_path,
        "table_name": safe_table,
        "rows_stored": len(df),
        "db_bytes": db_bytes,
    }


_SELECT_ONLY_RE = re.compile(r"^\s*SELECT\b", re.IGNORECASE)


def run_sql_query(
    query: str,
    db_path: str = "rental_data.db",
    on_progress=None,
) -> dict:
    """Really run a read-only SQL query against the stored database."""

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    if not _SELECT_ONLY_RE.match(query) or ";" in query:
        return {
            "success": False,
            "error": "Only a single SELECT statement is allowed (no ';', no writes).",
        }

    report(f"Running SQL query against {Path(db_path).name} ...")
    con = sqlite3.connect(db_path)
    try:
        try:
            cur = con.execute(query)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(20)
        except sqlite3.Error as exc:
            # A real query mistake (wrong table/column name, syntax error)
            # should read as "that query failed, try another one" — same as
            # the other real tools — not crash the entire run.
            report(f"Query failed ({exc}).")
            return {"success": False, "query": query, "error": str(exc)}
    finally:
        con.close()

    report(f"Query returned {len(rows)} row(s).")
    return {"query": query, "columns": columns, "rows": [list(r) for r in rows]}


# --- All agents: optional sketches ------------------------------------------


def make_sketch(content: str, kind: str = "ascii", title: str = "") -> dict:
    """Hand through a diagram the agent authored itself. No computation —
    `content` is either plain ASCII art or Graphviz DOT source, and `kind`
    tells the UI which one so it can render it appropriately."""
    kind = kind.lower() if kind.lower() in {"ascii", "dot"} else "ascii"
    return {"kind": kind, "title": title, "content": content}


# --- Tool schemas ------------------------------------------------------------

ATTEMPT_SCRAPE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "attempt_scrape",
        "description": (
            "Make one real, live request to a Swiss rental platform to "
            "actually test, live, whether scraping it is possible — don't "
            "just speculate about it. Checks the site's real robots.txt "
            "first, then sends the real GET request, and returns both plus "
            "the real response status/headers/timing so you can explain "
            "*why* it was blocked or allowed, not just that it was. Call "
            "this for one platform at a time; see the real result before "
            "deciding whether to try another."
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
                "resource_id": {
                    "type": "string",
                    "description": (
                        "The resource's id, exactly as returned by search_open_data (e.g. 'r0')."
                    ),
                },
            },
            "required": ["resource_id"],
        },
    },
}

DISCARD_DATASET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "discard_dataset",
        "description": (
            "Really delete the currently downloaded file because preview_data showed it's "
            "aggregated, not rental-apartment data, or otherwise unusable. Use this before "
            "searching for a replacement — never keep or reuse a file you've identified "
            "as unsuitable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Short real reason it's being discarded, e.g. 'aggregated by "
                        "municipality' or 'not rental data, it's museum exhibitions'."
                    ),
                }
            },
            "required": ["reason"],
        },
    },
}

PREVIEW_DATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "preview_data",
        "description": (
            "Really read and return the first N rows of the downloaded data file, when asked "
            "to show what the data actually looks like."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "n": {"type": "integer", "description": "How many rows to show, default 10."}
            },
            "required": [],
        },
    },
}

PROFILE_DATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "profile_data",
        "description": (
            "Load the downloaded real data file and compute real structure/"
            "quality stats: row count, column count, duplicate rows, missing "
            "values per column, the real column names, and each column's "
            "real data type — profiling to decide what needs cleaning and "
            "what the database schema should look like, not data analysis."
        ),
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}

CLEAN_DATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "clean_data",
        "description": (
            "Really clean the downloaded file: drop exact duplicate rows and/or "
            "rows missing values in key columns you name (based on what "
            "profile_data showed). Writes a real cleaned file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "drop_duplicates": {
                    "type": "boolean",
                    "description": "Drop exact duplicate rows. Default true.",
                },
                "drop_missing_in": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Column names to require non-missing values in (rows missing any of "
                        "these are dropped). Use real column names from profile_data."
                    ),
                },
            },
            "required": [],
        },
    },
}

STORE_TO_DATABASE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "store_to_database",
        "description": "Really write the cleaned file into a real local SQLite database table.",
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Table name to store the data in, e.g. 'apartments'.",
                },
            },
            "required": ["table_name"],
        },
    },
}

RUN_SQL_QUERY_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_sql_query",
        "description": (
            "Really run a read-only SQL SELECT query against the stored database "
            "to verify the data was stored correctly, e.g. 'SELECT COUNT(*) FROM "
            "apartments' or an AVG(...)/GROUP BY query. A single SELECT only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "A single SQL SELECT statement."},
            },
            "required": ["query"],
        },
    },
}

MAKE_SKETCH_SCHEMA = {
    "type": "function",
    "function": {
        "name": "make_sketch",
        "description": (
            "Optional: author a small diagram if you think it would help explain "
            "something (e.g. the data flow, a database schema) — either plain "
            "ASCII art or Graphviz DOT source. Not required; only use it when it "
            "genuinely clarifies something."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["ascii", "dot"],
                    "description": "Whether content is plain ASCII art or Graphviz DOT source.",
                },
                "title": {"type": "string", "description": "A short title for the sketch."},
                "content": {"type": "string", "description": "The ASCII art or DOT source itself."},
            },
            "required": ["kind", "content"],
        },
    },
}

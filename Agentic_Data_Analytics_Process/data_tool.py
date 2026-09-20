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


def _read_table(path: str, data_format: str, **kwargs) -> pd.DataFrame:
    fmt = data_format.upper()
    if fmt in {"XLSX", "XLS"}:
        return pd.read_excel(path, **kwargs)
    if fmt == "JSON":
        df = pd.read_json(path)
        return df.head(kwargs["nrows"]) if "nrows" in kwargs else df
    return pd.read_csv(path, sep=None, engine="python", on_bad_lines="skip", encoding="utf-8-sig", **kwargs)


# --- Data Analyst: Collecting data -----------------------------------------


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


def discard_dataset(path: str = "downloaded_dataset.csv", reason: str = "", on_progress=None) -> dict:
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


def preview_data(path: str = "downloaded_dataset.csv", data_format: str = "CSV", n: int = 10, on_progress=None) -> dict:
    """Really read the first n rows of the file."""
    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Reading the first {n} rows of {Path(path).name} ...")
    df = _read_table(path, data_format, nrows=n)

    columns = [str(c) for c in df.columns]
    rows = df.astype(object).where(df.notna(), None).values.tolist()

    report(f"Got the first {len(rows)} rows.")
    return {"columns": columns, "rows": rows}


def profile_data(path: str = "downloaded_dataset.csv", data_format: str = "CSV", on_progress=None) -> dict:
    """Load the real file and compute real structure/quality stats — profiling
    to decide what needs cleaning, not data analysis."""
    def report(stage: str):
        if on_progress:
            on_progress(stage)

    report(f"Profiling {Path(path).name} ...")
    df = _read_table(path, data_format)

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
    df = _read_table(source_path, data_format)
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
    df = pd.read_csv(source_path)

    safe_table = re.sub(r"[^A-Za-z0-9_]", "_", table_name) or "apartments"
    con = sqlite3.connect(db_path)
    try:
        df.to_sql(safe_table, con, if_exists="replace", index=False)
        con.commit()
    finally:
        con.close()

    db_bytes = Path(db_path).stat().st_size
    report(f"Stored {len(df):,} rows in table '{safe_table}' ({db_bytes:,} bytes).")
    return {"db_path": db_path, "table_name": safe_table, "rows_stored": len(df), "db_bytes": db_bytes}


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
        return {"success": False, "error": "Only a single SELECT statement is allowed (no ';', no writes)."}

    report(f"Running SQL query against {Path(db_path).name} ...")
    con = sqlite3.connect(db_path)
    try:
        cur = con.execute(query)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = cur.fetchmany(20)
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

DISCARD_DATASET_SCHEMA = {
    "type": "function",
    "function": {
        "name": "discard_dataset",
        "description": (
            "Really delete the currently downloaded file because preview_data showed it's "
            "aggregated, not rental-apartment data, or otherwise unusable. Use this before "
            "searching for a replacement — never keep or reuse a file you've identified as unsuitable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Short real reason it's being discarded, e.g. 'aggregated by municipality' or 'not rental data, it's museum exhibitions'."}
            },
            "required": ["reason"],
        },
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
                "drop_duplicates": {"type": "boolean", "description": "Drop exact duplicate rows. Default true."},
                "drop_missing_in": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Column names to require non-missing values in (rows missing any of these are dropped). Use real column names from profile_data.",
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
                "table_name": {"type": "string", "description": "Table name to store the data in, e.g. 'apartments'."},
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
                "kind": {"type": "string", "enum": ["ascii", "dot"], "description": "Whether content is plain ASCII art or Graphviz DOT source."},
                "title": {"type": "string", "description": "A short title for the sketch."},
                "content": {"type": "string", "description": "The ASCII art or DOT source itself."},
            },
            "required": ["kind", "content"],
        },
    },
}

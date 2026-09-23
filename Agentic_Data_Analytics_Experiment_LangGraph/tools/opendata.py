"""Open-data tools the Data Analyst can call while collecting data.

No local fallback dataset, nothing faked, and no pre-decided choices: these
tools report real results and hand real options back to the agent.

1. `search_open_data` queries opendata.swiss's genuinely public CKAN API
   for real Swiss housing/rental datasets and returns each candidate's
   real downloadable resources (format + URL) — the agent picks which
   dataset/resource to use, nothing is pre-selected here.
2. `download_dataset` really downloads whichever resource URL the agent
   chose.
3. `discard_dataset` really deletes a download that turned out unsuitable.

(Scraping lives in scraper.py: the agent writes its own scraper code and
really runs it.)
"""

from pathlib import Path

import requests

REQUEST_TIMEOUT_SECONDS = 8
DOWNLOAD_TIMEOUT_SECONDS = 25
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
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


# --- Data Analyst: Collecting data -----------------------------------------

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
        # something else" — same as download_dataset — not
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

"""Tools that let the Data Analyst write its own scraper and really run it
(their schemas — what the model is told — are in schemas.py).

1. `write_scraper_code` — the agent hands in a complete Python script; it's
   statically checked (see `check_scraper_code`) and saved as
   data/scrapers/scraper_vN.py so students can read every version.
2. `run_scraper` — really runs one saved version in a separate Python
   process: time limit, a fresh working directory, and an environment
   stripped of every secret (no OPENAI_API_KEY). The script's only way to
   the web is sandbox/scraper_kit.py, which enforces robots.txt, the domain
   allowlist, delays, a request budget, and stop-at-first-block in code.

The code check and the runner are shared with the data-preparation
scripts (see sandbox.py).
"""

import json
from pathlib import Path

import pandas as pd

from tools.sandbox import SAFE_STDLIB_MODULES, base_env, check_code, run_script, save_script

RUN_TIMEOUT_SECONDS = 180
MAX_REQUESTS_PER_RUN = 15
MAX_ROWS = 150

ALLOWED_MODULES = {"scraper_kit", "bs4", *SAFE_STDLIB_MODULES}
SCRAPER_KIT_PUBLIC = {
    "polite_get",
    "save_rows",
    "ScrapeBlocked",
    "Page",
    "FIELDS",
    "ALLOWED_DOMAINS",
    "MAX_REQUESTS",
    "MAX_ROWS",
}


def check_scraper_code(code: str) -> list[str]:
    """Static whitelist check of agent-written scraper code (see
    sandbox.check_code). Returns the problems found (empty list = OK to run)."""
    return check_code(code, ALLOWED_MODULES, {"scraper_kit": SCRAPER_KIT_PUBLIC})


def save_scraper_code(code: str, scrapers_dir: Path, version: int) -> dict:
    """Statically check the code and save it as scraper_v{version}.py
    (saved either way, so a rejected version can still be read)."""
    path = scrapers_dir / f"scraper_v{version}.py"
    return {"version": version, **save_script(code, path, check_scraper_code(code))}


def scraper_env(log_path: Path, out_path: Path) -> dict:
    """The scraper process's whole environment: a short passthrough list
    plus scraper_kit's settings — no API keys or other secrets."""
    env = base_env()
    env.update(
        {
            "SCRAPER_KIT_LOG": str(log_path),
            "SCRAPER_KIT_OUT": str(out_path),
            "SCRAPER_KIT_MAX_REQUESTS": str(MAX_REQUESTS_PER_RUN),
            "SCRAPER_KIT_MAX_ROWS": str(MAX_ROWS),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def run_scraper_code(
    script_path: Path, work_dir: Path, out_path: Path, on_progress=None
) -> dict:
    """Really run one saved scraper version (see sandbox.run_script) and
    return what actually happened (exit code, output tail, request log,
    rows saved)."""
    out_path.unlink(missing_ok=True)
    env = scraper_env(work_dir / "requests_log.jsonl", out_path)
    result = run_script(script_path, work_dir, env, RUN_TIMEOUT_SECONDS, on_progress)

    rows_saved, columns, sample_rows = 0, [], []
    if out_path.exists():
        try:
            df = pd.read_csv(out_path)
            rows_saved = len(df)
            columns = list(df.columns)
            sample_rows = json.loads(df.head(5).to_json(orient="values"))
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            pass

    if on_progress:
        pages = [e for e in result["requests"] if e.get("kind") == "page" and not e.get("blocked")]
        outcome = "timed out" if result["timed_out"] else f"exit code {result['exit_code']}"
        on_progress(
            f"{script_path.name}: {outcome}, {len(pages)} page(s) fetched, "
            f"{rows_saved} row(s) saved, {result['elapsed_seconds']} s."
        )
    return {**result, "rows_saved": rows_saved, "columns": columns, "sample_rows": sample_rows}

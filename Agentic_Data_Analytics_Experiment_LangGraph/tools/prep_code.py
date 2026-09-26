"""Tools that let the agents write their own data-preparation code and
really run it — the Data Engineer's cleaning script, then the Data
Analyst's enrichment script (their schemas are in schemas.py).

1. `write_prep_code` — the agent hands in a complete pandas script; it's
   statically checked (see `check_prep_code`) and saved as
   data/prep/clean_vN.py or enrich_vN.py so students can read every version.
2. `run_prep_code` — really runs one saved version in a separate Python
   process (see sandbox.run_script). The script reads this stage's input
   and saves its output only through sandbox/prep_kit.py; its only way to
   the web is sandbox/scraper_kit.py (flatfox.ch and the federal geodata
   API api3.geo.admin.ch, robots.txt, delays, request budget,
   stop-at-first-block).

Nothing about *what* to clean or which features to add is decided here —
only whether the result is still a usable listing-level table
(`judge_prep_output`), checked in code on every run.
"""

import json
from pathlib import Path

import pandas as pd

from tools.sandbox import SAFE_STDLIB_MODULES, base_env, check_code, run_script, save_script
from tools.validation import implausible_values

RUN_TIMEOUT_SECONDS = 240
MAX_REQUESTS_PER_RUN = 300  # enough for one geodata lookup per apartment
PREP_DOMAINS = ("flatfox.ch", "api3.geo.admin.ch")
# Cleaning that drops more than this share of the listings is almost
# always a filter bug, not real dirt in the data.
MAX_DROPPED_SHARE = 0.5
# With this many listings, a new column that has the same value in every
# row is a bug (a live run accepted seven amenity flags that were all False
# because the regex could never match), not a fact about the data.
MIN_ROWS_FOR_CONSTANT_CHECK = 10
# The rent is what the later model learns to predict: a missing rent may be
# left missing or the listing dropped, but never filled in (e.g. with a
# median) — that would invent training labels. Live runs did exactly that.
TARGET_COLUMNS = ("rent_gross_chf", "rent_net_chf")
# What a missing value turns into when a script calls .astype(str) before
# a text fix (.str.title() etc.) — a live run stored the street "Nan".
STRINGIFIED_MISSING = {"nan", "none", "null", "<na>", "nat"}

ALLOWED_MODULES = {"prep_kit", "scraper_kit", "pandas", "numpy", *SAFE_STDLIB_MODULES}
PREP_KIT_PUBLIC = {"load_data", "save_data"}
SCRAPER_KIT_PUBLIC = {"polite_get", "ScrapeBlocked", "Page", "ALLOWED_DOMAINS", "MAX_REQUESTS"}

# pandas/numpy can read and write files (and URLs) on their own — only
# prep_kit may do that, so their file I/O and string-evaluation entry
# points are off limits, plus the submodules that lead to os/io.
_FORBIDDEN_ATTRIBUTES = {
    "to_csv", "to_excel", "to_json", "to_pickle", "to_parquet", "to_sql", "to_hdf",
    "to_feather", "to_stata", "to_html", "to_latex", "to_markdown", "to_clipboard",
    "to_xml", "to_orc", "tofile", "load", "save", "savez", "savez_compressed",
    "loadtxt", "savetxt", "genfromtxt", "fromfile", "memmap", "DataSource",
    "ExcelWriter", "ExcelFile", "HDFStore", "eval", "query", "io", "lib", "os",
    "sys", "ctypeslib", "testing", "compat", "util", "utils",
}


def _forbidden_attribute(name: str) -> bool:
    return name in _FORBIDDEN_ATTRIBUTES or name.startswith("read_")


def check_prep_code(code: str) -> list[str]:
    """Static whitelist check of an agent-written preparation script.
    Returns the problems found (empty list = OK to run)."""
    problems = check_code(
        code,
        ALLOWED_MODULES,
        {"prep_kit": PREP_KIT_PUBLIC, "scraper_kit": SCRAPER_KIT_PUBLIC},
        forbidden_attributes=_forbidden_attribute,
    )
    if not problems and "save_data" not in code:
        problems.append("the script never calls prep_kit.save_data(df) — its result would be lost")
    return problems


def save_prep_code(code: str, prep_dir: Path, stage: str, version: int) -> dict:
    """Statically check the code and save it as {stage}_v{version}.py
    (saved either way, so a rejected version can still be read)."""
    path = prep_dir / f"{stage}_v{version}.py"
    return {"stage": stage, "version": version, **save_script(code, path, check_prep_code(code))}


def prep_env(log_path: Path, in_path: Path, in_format: str, out_path: Path) -> dict:
    """The script process's whole environment: the passthrough list plus
    prep_kit's and scraper_kit's settings — no API keys or other secrets."""
    # Absolute paths: the script runs with its own working directory.
    in_path, out_path, log_path = in_path.resolve(), out_path.resolve(), log_path.resolve()
    env = base_env()
    env.update(
        {
            "PREP_KIT_IN": str(in_path),
            "PREP_KIT_IN_FORMAT": in_format,
            "PREP_KIT_OUT": str(out_path),
            "SCRAPER_KIT_LOG": str(log_path),
            # scraper_kit's own CSV output isn't used by prep scripts
            "SCRAPER_KIT_OUT": str(log_path.with_name("unused_scraper_rows.csv")),
            "SCRAPER_KIT_DOMAINS": ",".join(PREP_DOMAINS),
            "SCRAPER_KIT_MAX_REQUESTS": str(MAX_REQUESTS_PER_RUN),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def _table_facts(df: pd.DataFrame) -> dict:
    return {
        "rows": len(df),
        "columns": [str(c) for c in df.columns],
        "dtypes": {str(c): str(t) for c, t in df.dtypes.items()},
        "missing_values": {str(c): int(n) for c, n in df.isna().sum().items() if n > 0},
    }


def _read_input(in_path: Path, in_format: str) -> pd.DataFrame:
    fmt = in_format.upper()
    if fmt in {"XLSX", "XLS"}:
        return pd.read_excel(in_path)
    if fmt == "JSON":
        return pd.read_json(in_path)
    return pd.read_csv(in_path, sep=None, engine="python", encoding="utf-8-sig")


def _filled_target_values(before: pd.DataFrame, after: pd.DataFrame) -> dict[str, int]:
    """Per rent column: how many listings had no rent in the input but have
    one in the output (matched by listing_id)."""
    if "listing_id" not in before.columns or "listing_id" not in after.columns:
        return {}
    filled = {}
    for col in TARGET_COLUMNS:
        if col not in before.columns or col not in after.columns:
            continue
        was_missing = set(before.loc[before[col].isna(), "listing_id"])
        now_there = set(after.loc[after[col].notna(), "listing_id"])
        if was_missing & now_there:
            filled[col] = len(was_missing & now_there)
    return filled


def _stringified_missing(before: pd.DataFrame, after: pd.DataFrame) -> dict[str, int]:
    """Per text column: how many more cells the output has than the input
    whose value is the text 'nan'/'None'/… — a real missing value turned
    into a string that looks like data."""
    def count(df: pd.DataFrame, col) -> int:
        if col not in df.columns or pd.api.types.is_numeric_dtype(df[col]):
            return 0
        return int(df[col].dropna().astype(str).str.strip().str.lower()
                   .isin(STRINGIFIED_MISSING).sum())

    grown = {str(c): count(after, c) - count(before, c) for c in after.columns}
    return {c: n for c, n in grown.items() if n > 0}


def _distinct_values(df: pd.DataFrame, columns: list[str]) -> dict[str, int]:
    """How many different (non-missing) values each of `columns` has."""
    return {c: int(df[c].nunique(dropna=True)) for c in columns if c in df.columns}


def run_prep_code(
    script_path: Path,
    work_dir: Path,
    in_path: Path,
    in_format: str,
    out_path: Path,
    on_progress=None,
) -> dict:
    """Really run one saved preparation script and return what actually
    happened: exit code, output tail, request log, and the real
    before/after shape of the data (rows, columns added/removed, dtypes,
    missing values, a sample)."""
    out_path.unlink(missing_ok=True)
    env = prep_env(work_dir / "requests_log.jsonl", in_path, in_format, out_path)
    result = run_script(script_path, work_dir, env, RUN_TIMEOUT_SECONDS, on_progress)

    before_df = _read_input(in_path, in_format)
    before = _table_facts(before_df)
    result.update(
        {
            "rows_before": before["rows"],
            "columns_before": before["columns"],
            "saved": False,
            "rows_after": 0,
            "columns": [],
            "added_columns": [],
            "removed_columns": [],
            "dtypes": {},
            "missing_values": {},
            "sample_rows": [],
        }
    )
    if out_path.exists():
        try:
            df = pd.read_csv(out_path)
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            df = None
        if df is not None:
            after = _table_facts(df)
            result.update(
                {
                    "saved": True,
                    "rows_after": after["rows"],
                    "columns": after["columns"],
                    "added_columns": [c for c in after["columns"] if c not in before["columns"]],
                    "removed_columns": [c for c in before["columns"] if c not in after["columns"]],
                    "dtypes": after["dtypes"],
                    "missing_values": after["missing_values"],
                    "sample_rows": json.loads(df.head(5).to_json(orient="values")),
                    "listing_id_unique": (
                        bool(df["listing_id"].is_unique) if "listing_id" in df.columns else None
                    ),
                    "filled_target_values": _filled_target_values(before_df, df),
                    "stringified_missing": _stringified_missing(before_df, df),
                }
            )
            result["added_column_values"] = _distinct_values(df, result["added_columns"])
            result["implausible"] = implausible_values(df)

    if on_progress:
        outcome = "timed out" if result["timed_out"] else f"exit code {result['exit_code']}"
        on_progress(
            f"{script_path.name}: {outcome}, {result['rows_before']} → {result['rows_after']} "
            f"rows, {len(result['added_columns'])} column(s) added, "
            f"{result['elapsed_seconds']} s."
        )
    return result


def _run_failure(run: dict) -> str:
    """Why the run produced no output at all, if it didn't."""
    if run["timed_out"]:
        return f"the script hit the {RUN_TIMEOUT_SECONDS} s time limit"
    if run["exit_code"] != 0:
        return "the script crashed (see the traceback in output_tail)"
    if not run["saved"]:
        return "prep_kit.save_data(df) was never reached, so there's no output"
    if run["rows_after"] == 0:
        return "the saved table has 0 rows"
    return ""


def _listing_problem(run: dict) -> str:
    """Whether every row is still one listing, and no rent was made up."""
    if "listing_id" in run["columns_before"]:
        if "listing_id" not in run["columns"]:
            return "listing_id was removed — every row must stay traceable to its listing"
        if run.get("listing_id_unique") is False:
            return "listing_id is no longer unique — one row per listing is required"
    filled = run.get("filled_target_values") or {}
    if filled:
        detail = ", ".join(f"{col}: {n} listings" for col, n in filled.items())
        return (
            f"it filled in rents that were missing in the input ({detail}) — the rent is "
            "what the price model will learn to predict, so a made-up rent is a made-up "
            "training label; leave it missing or drop listings with no rent at all"
        )
    stringified = run.get("stringified_missing") or {}
    if stringified:
        detail = ", ".join(f"{col}: {n}" for col, n in stringified.items())
        return (
            f"missing values were turned into the text 'nan'/'None' ({detail}) — that "
            "happens when .astype(str) runs before a text fix; apply string methods only "
            "to the non-missing values (e.g. df[col].str.strip() on an object column "
            "keeps NaN as NaN) so a missing value stays missing"
        )
    return ""


def _stage_problem(stage: str, run: dict) -> str:
    """The stage's own rule: cleaning keeps most rows, enrichment keeps
    every row and adds a column."""
    if stage == "clean":
        dropped = 1 - run["rows_after"] / run["rows_before"] if run["rows_before"] else 0
        if dropped > MAX_DROPPED_SHARE:
            return (
                f"it dropped {dropped:.0%} of the listings ({run['rows_before']} → "
                f"{run['rows_after']}) — that's almost always a filter or parsing bug "
                "(print how many rows each step drops), not real dirt in the data"
            )
    if stage == "enrich":
        if run["rows_after"] != run["rows_before"]:
            return (
                f"enrichment must keep exactly one row per listing, but the row count "
                f"changed ({run['rows_before']} → {run['rows_after']}) — a merge probably "
                "duplicated or dropped rows"
            )
        if not run["added_columns"]:
            return "no new column was added, so nothing was enriched"
        constant = [c for c, n in (run.get("added_column_values") or {}).items() if n <= 1]
        if constant and run["rows_after"] >= MIN_ROWS_FOR_CONSTANT_CHECK:
            return (
                f"new column(s) {', '.join(constant)} have the same value (or none) for every "
                f"one of the {run['rows_after']} listings, so they carry no information — "
                "almost always a pattern that never matches: print how many rows each "
                "keyword/regex hits, check the case of the text you search, and in a raw "
                "string write r'\\bword', not r'\\\\bword' (that looks for a literal backslash)"
            )
    return ""


def judge_prep_output(stage: str, run: dict) -> tuple[bool, str]:
    """Whether a run's output is still a usable listing-level table for
    this stage. Returns (ok, reason if not)."""
    reason = _run_failure(run) or _listing_problem(run) or _stage_problem(stage, run)
    return not reason, reason

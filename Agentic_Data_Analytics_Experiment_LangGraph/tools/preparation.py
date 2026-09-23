"""Data-preparation tools the Data Engineer (and, for previews, the Data
Analyst) can call — no analysis/interpretation here, just structure/quality
checks and real cleaning/storage:

1. `preview_data` really reads the first N rows of the downloaded file.
2. `profile_data` loads the file and computes real structure/quality stats
   (row count, duplicate rows, missing values) — profiling *for cleaning*,
   not data analysis.
3. `clean_data` really drops duplicate rows and/or rows missing key
   columns, writing a real cleaned file.
4. `store_to_database` really writes the cleaned file into a real local
   SQLite database.
5. `run_sql_query` really runs a real (read-only) SQL query against that
   database and returns real rows.

All three agents:
6. `make_sketch` hands through a diagram the agent authored itself (plain
   ASCII or Graphviz DOT source) if it finds a sketch useful — no
   computation, just structured enough for the UI to render it distinctly.
"""

import re
import sqlite3
from pathlib import Path

import pandas as pd


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

"""What the agents can put in front of the class besides words — the
`show_to_class` tool. Every exhibit is built from real files, never from
values the model types itself:

- `rows_exhibit`: a few real rows (chosen columns) of a dataset file
- `single_case_exhibit`: ONE listing, before and after preparation, field
  by field — e.g. its description text next to the values the agents'
  code derived from it
- `code_exhibit`: a few real lines of a script an agent wrote, with line
  numbers

The web app is a teaching demo: students learn more from one concrete
case than from an agent saying "the enrichment worked".
"""

from pathlib import Path

import pandas as pd

from tools.preparation import read_table

MAX_ROWS = 8
MAX_CODE_LINES = 40
MAX_TEXT_CHARS = 1500


def _cell(value):
    """A JSON-friendly cell: NaN -> None, long text cut."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str) and len(value) > MAX_TEXT_CHARS:
        return value[:MAX_TEXT_CHARS] + " …"
    return value.item() if hasattr(value, "item") else value


def _pick_columns(df: pd.DataFrame, columns: list | None) -> tuple[list[str], list[str]]:
    """The requested columns that exist (all if none requested), and the
    requested ones that don't."""
    if not columns:
        return [str(c) for c in df.columns], []
    present = [c for c in columns if c in df.columns]
    return present, [c for c in columns if c not in df.columns]


def rows_exhibit(path: str, data_format: str, columns: list | None, n: int,
                 listing_ids: list | None = None) -> dict:
    """A few real rows of a dataset file."""
    df = read_table(path, data_format)
    if listing_ids and "listing_id" in df.columns:
        df = df[df["listing_id"].isin(listing_ids)]
    cols, unknown = _pick_columns(df, columns)
    if not cols:
        return {"error": f"none of these columns exist: {', '.join(unknown)}"}
    sample = df[cols].head(max(1, min(n, MAX_ROWS)))
    return {
        "kind": "rows",
        "file": Path(path).name,
        "total_rows": len(df),
        "columns": cols,
        "rows": [[_cell(v) for v in row] for row in sample.itertuples(index=False)],
        "unknown_columns": unknown,
    }


def single_case_exhibit(before_path: str, before_format: str, after_path: str,
                        listing_id, columns: list | None) -> dict:
    """ONE listing, field by field: its value in the collected data, its
    value now, and whether the preparation added or changed it."""
    before = read_table(before_path, before_format)
    after = read_table(after_path, "CSV") if after_path != before_path else before
    if "listing_id" not in before.columns or "listing_id" not in after.columns:
        return {"error": "the data has no listing_id column to pick one listing by"}
    key = str(listing_id)
    old_rows = before[before["listing_id"].astype(str) == key]
    new_rows = after[after["listing_id"].astype(str) == key]
    if new_rows.empty and old_rows.empty:
        some = ", ".join(map(str, after["listing_id"].head(5)))
        return {"error": f"no listing with listing_id {listing_id} (e.g. {some})"}
    names = list(dict.fromkeys([*map(str, before.columns), *map(str, after.columns)]))
    wanted = [c for c in columns if c in names] if columns else names
    old = old_rows.iloc[0] if not old_rows.empty else pd.Series(dtype=object)
    new = new_rows.iloc[0] if not new_rows.empty else pd.Series(dtype=object)
    return {
        "kind": "single_case",
        "listing_id": key,
        "dropped": new_rows.empty,
        "fields": [_field(col, old, new, before.columns, after.columns) for col in wanted],
        "unknown_columns": [c for c in columns or [] if c not in names],
    }


def _same_value(was, now) -> bool:
    """Equal as values — 3 and 3.0 count as the same, only a real change
    (a parsed number, a fixed text) counts as changed."""
    try:
        return float(was) == float(now)
    except (TypeError, ValueError):
        return str(was) == str(now)


def _field(col: str, old: pd.Series, new: pd.Series, before_cols, after_cols) -> dict:
    """One column of a single case: its value as collected, now, and
    whether the preparation added, changed or removed it."""
    was = _cell(old.get(col)) if col in before_cols else None
    now = _cell(new.get(col)) if col in after_cols else None
    if col not in before_cols:
        status = "new"
    elif col not in after_cols:
        status = "removed"
    elif not _same_value(was, now):
        status = "changed"
    else:
        status = "same"
    return {"column": col, "before": was, "after": now, "status": status}


def code_exhibit(script_path: str, start_line: int | None, end_line: int | None) -> dict:
    """A few real lines of a saved script, numbered as in the file."""
    lines = Path(script_path).read_text(encoding="utf-8").splitlines()
    start = max(1, start_line or 1)
    end = min(len(lines), end_line or start + MAX_CODE_LINES - 1, start + MAX_CODE_LINES - 1)
    if start > len(lines):
        return {"error": f"{Path(script_path).name} has only {len(lines)} lines"}
    return {
        "kind": "code",
        "file": Path(script_path).name,
        "start_line": start,
        "lines": lines[start - 1 : end],
        "total_lines": len(lines),
    }

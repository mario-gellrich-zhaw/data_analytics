"""Code-level checks on whether a dataset really is what Step 3 needs:
individual-apartment rental listings (one row per listing), not a
pre-aggregated statistics table or a half-empty scrape.

These run on every download and every scraper run regardless of what the
agents themselves conclude — prompting alone proved unreliable here (the
model sometimes skipped its own verification step and declared success on
an obviously aggregated file).
"""

from pathlib import Path

import pandas as pd

from tools.preparation import preview_data

# Column-name fragments that reliably indicate a pre-aggregated statistics
# table (one row per stratum — e.g. per district/year/room-count — not per
# apartment) even when every column is fully named, so the "≥50% unnamed
# columns" check below can't catch it. Real Swiss rent-price tables (e.g.
# opendata.swiss's Mietpreise dataset) look exactly like this: named
# mean/quantile columns, plenty of rows, zero unnamed columns — and slip
# straight through without this. Deliberately narrow (no "count"/"min"/
# "max"/"sum"/"total" — those show up in legitimate per-listing fields too,
# e.g. "room_count") so it only fires on genuine statistical-summary terms.
AGGREGATE_COLUMN_HINTS = (
    "mean",
    "median",
    "average",
    "quantile",
    "percentile",
    "qu25",
    "qu50",
    "qu75",
    "stdev",
    "stddev",
    "variance",
)
MIN_LISTING_ROWS = 15  # a whole-canton listings dataset should clear this easily
# Scraped output only counts as a dataset if each of these is mostly
# filled in (any one column of a group is enough) — a scraper that maps the
# wrong field names, or keeps parking spaces/commercial units alongside
# apartments, produces a well-shaped but half-empty table.
SCRAPED_REQUIRED_FIELDS = (
    ("listing_id",),
    ("rent_gross_chf", "rent_net_chf"),
    ("rooms",),
    ("zip", "city"),
)
MIN_FILLED_SHARE = 0.8


def looks_like_listing_data(path: str, data_format: str) -> tuple[bool, str]:
    """Whether a file looks like individual-apartment listings rather than
    a pre-aggregated statistics table. Returns (ok, reason if not)."""
    # This can't judge topic/semantics, but it reliably catches the most
    # common real failure shapes seen live: multi-header statistics exports
    # (mostly "Unnamed: N" columns after pandas parses them),
    # named-but-aggregated statistics tables (mean/quantile columns,
    # e.g. opendata.swiss's Mietpreise dataset), and pivot/summary
    # tables (a handful of rows).
    result = preview_data(path=path, data_format=data_format, n=50)
    if result.get("error"):
        return False, f"the file couldn't even be read as a table ({result['error']})"
    columns = result["columns"]
    n_rows = len(result["rows"])
    if not columns:
        return False, "the file has no readable columns"
    unnamed = sum(1 for c in columns if str(c).lower().startswith("unnamed"))
    if unnamed / len(columns) >= 0.5:
        return False, (
            f"{unnamed}/{len(columns)} columns came back unnamed — this looks like a "
            "multi-header statistics export (e.g. a pivoted year-by-year table), not "
            "one row per apartment listing"
        )
    aggregate_hits = [
        c for c in columns if any(hint in str(c).lower() for hint in AGGREGATE_COLUMN_HINTS)
    ]
    if len(aggregate_hits) >= 2:
        return False, (
            f"columns like {', '.join(map(str, aggregate_hits[:4]))} look like "
            "statistical aggregates (mean/median/quantile), not per-apartment fields — "
            "this is a pre-aggregated summary table (one row per stratum, e.g. per "
            "district/year/room-count), not one row per apartment listing"
        )
    if n_rows < MIN_LISTING_ROWS:
        return False, (
            f"only {n_rows} rows — far too few to be individual apartment listings for "
            "the canton of Zurich, this looks like a small summary/pivot table"
        )
    return True, ""


def filled_share(csv_path: Path) -> dict[str, float]:
    """Share of non-empty values per column of a scraped CSV."""
    df = pd.read_csv(csv_path)
    return {col: round(float(df[col].notna().mean()), 2) for col in df.columns}


def check_scraped_fields(shares: dict) -> tuple[bool, str]:
    """Whether the key listing fields of a scraped CSV are really filled
    in (`shares` as returned by `filled_share`)."""
    missing = []
    for group in SCRAPED_REQUIRED_FIELDS:
        best = max(shares.get(col, 0) for col in group)
        if best < MIN_FILLED_SHARE:
            missing.append(f"{' or '.join(group)} ({best:.0%} filled)")
    if missing:
        return False, (
            f"key field(s) not filled in for at least {MIN_FILLED_SHARE:.0%} of rows: "
            f"{', '.join(missing)} — either the scraper maps the wrong source field "
            "names (check response_structure), or it keeps rows that aren't rental "
            "apartments (parking spaces, commercial units, properties for sale)"
        )
    return True, ""

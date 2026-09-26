"""Code-level checks on whether a dataset really is what Step 3 needs:
individual-apartment rental listings (one row per listing), not a
pre-aggregated statistics table or a half-empty scrape.

These run on every download and every scraper run regardless of what the
agents themselves conclude — prompting alone proved unreliable here (the
model sometimes skipped its own verification step and declared success on
an obviously aggregated file).
"""

import unicodedata
from datetime import datetime
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


# Plausible ranges for a rental apartment in the canton of Zurich — outside
# them a value is almost always a typo in the listing (a live run kept
# "523 m²" for a flat whose own text said "ca. 53 m²", and floor -3 for
# "3. Obergeschoss") or a parsing slip, worth a look before it trains a model.
PLAUSIBLE_RANGES = {
    "living_space_m2": (10, 400),
    "rooms": (1, 12),
    "floor": (-1, 40),
    "rent_gross_chf": (300, 20000),
    "rent_net_chf": (300, 20000),
}
MAX_M2_PER_ROOM = 100
MAX_EXAMPLE_IDS = 3
# Building years: a live run kept year_built = 0, which enrichment turned
# into a "property age" of 2026 years. Listings are published ahead of
# completion, so a few years ahead is still fine.
EARLIEST_BUILDING_YEAR = 1500
YEARS_AHEAD = 3
YEAR_COLUMNS = ("year_built", "year_renovated")
# The same flat posted several times under different listing ids — a live
# run had one address three times with the same rent and room count.
DUPLICATE_KEYS = ("street", "rooms", "rent_gross_chf")
# Free-text columns whose spellings should agree ("Zürich" vs "Zurich").
SPELLING_COLUMNS = ("city",)


def _examples(df: pd.DataFrame, mask: pd.Series) -> str:
    if "listing_id" not in df.columns:
        return ""
    ids = ", ".join(str(i) for i in df.loc[mask, "listing_id"].head(MAX_EXAMPLE_IDS))
    return f" (e.g. listing {ids})"


def implausible_values(df: pd.DataFrame) -> list[str]:
    """Values outside what a Zurich rental apartment can plausibly have,
    one short line per problem with a count and example listings. Only
    reported, never rejected: whether to fix, null or drop them is the
    cleaning agent's call."""
    problems = []
    numeric = {c: pd.to_numeric(df[c], errors="coerce") for c in PLAUSIBLE_RANGES if c in df}
    for col, values in numeric.items():
        low, high = PLAUSIBLE_RANGES[col]
        mask = (values < low) | (values > high)
        if mask.any():
            problems.append(
                f"{col}: {int(mask.sum())} value(s) outside {low}–{high}{_examples(df, mask)}"
            )
    latest = datetime.now().year + YEARS_AHEAD
    for col in (c for c in YEAR_COLUMNS if c in df.columns):
        years = pd.to_numeric(df[col], errors="coerce")
        mask = (years < EARLIEST_BUILDING_YEAR) | (years > latest)
        if mask.any():
            problems.append(
                f"{col}: {int(mask.sum())} value(s) outside {EARLIEST_BUILDING_YEAR}–{latest}"
                f"{_examples(df, mask)}"
            )
    if all(c in df.columns for c in YEAR_COLUMNS):
        built, renovated = (pd.to_numeric(df[c], errors="coerce") for c in YEAR_COLUMNS)
        mask = renovated < built
        if mask.any():
            problems.append(
                f"year_renovated before year_built in {int(mask.sum())} listing(s)"
                f"{_examples(df, mask)}"
            )
    if "living_space_m2" in numeric and "rooms" in numeric:
        per_room = numeric["living_space_m2"] / numeric["rooms"].where(numeric["rooms"] > 0)
        mask = per_room > MAX_M2_PER_ROOM
        if mask.any():
            problems.append(
                f"living_space_m2 / rooms: {int(mask.sum())} listing(s) with more than "
                f"{MAX_M2_PER_ROOM} m² per room{_examples(df, mask)} — often a typo "
                "(check the listing's own text)"
            )
    return problems


def likely_duplicates(df: pd.DataFrame) -> list[str]:
    """The same flat listed more than once under different listing ids:
    same street, room count and rent."""
    if not all(c in df.columns for c in (*DUPLICATE_KEYS, "listing_id")):
        return []
    keyed = df.dropna(subset=list(DUPLICATE_KEYS))
    keyed = keyed.assign(_street=keyed["street"].astype(str).str.lower().str.strip())
    groups = keyed.groupby(["_street", "rooms", "rent_gross_chf"])["listing_id"].nunique()
    repeated = groups[groups > 1]
    if repeated.empty:
        return []
    first = repeated.index[0]
    ids = keyed.loc[
        (keyed["_street"] == first[0]) & (keyed["rooms"] == first[1])
        & (keyed["rent_gross_chf"] == first[2]), "listing_id",
    ].head(MAX_EXAMPLE_IDS)
    extra = int(repeated.sum() - len(repeated))
    return [
        f"likely duplicates: {len(repeated)} flat(s) listed more than once under different "
        f"listing ids ({extra} extra row(s)) — same street, rooms and rent, e.g. listings "
        f"{', '.join(map(str, ids))}"
    ]


def _spelling_key(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(plain.lower().split())


def inconsistent_spellings(df: pd.DataFrame) -> list[str]:
    """Names that differ only in accents, case or spacing ("Zürich" /
    "Zurich") — one place counted as two."""
    problems = []
    for col in (c for c in SPELLING_COLUMNS if c in df.columns):
        values = df[col].dropna().astype(str)
        variants = values.groupby(values.map(_spelling_key)).unique()
        mixed = [sorted(v) for v in variants if len(v) > 1]
        if mixed:
            shown = "; ".join(" / ".join(f"'{s}'" for s in v) for v in mixed[:MAX_EXAMPLE_IDS])
            problems.append(f"{col}: the same name spelled differently — {shown}")
    return problems


def data_quality_issues(df: pd.DataFrame) -> list[str]:
    """Everything worth a look before this data trains a model: implausible
    values, likely duplicate listings and inconsistent spellings — one
    short line each. Only reported, never rejected: what to do about them
    is the cleaning agent's call."""
    return implausible_values(df) + likely_duplicates(df) + inconsistent_spellings(df)

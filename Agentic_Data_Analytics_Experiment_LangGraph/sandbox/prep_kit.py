"""The only way a data-preparation script the agents write themselves (see
tools/prep_code.py) reads the dataset and hands back its result — the
script itself may not touch files (no open, no pandas read_*/to_*), so the
app decides in code which file goes in and where the result lands.

Usage from agent code:

    import prep_kit
    df = prep_kit.load_data()          # this stage's input, as a DataFrame
    ...                                # clean / enrich it with pandas
    prep_kit.save_data(df)             # this stage's output

The web (e.g. a per-apartment lookup) goes through scraper_kit.polite_get,
with the same robots.txt / allowlist / stop-at-first-block rules as the
scraper.
"""

import os

import pandas as pd

IN_PATH = os.environ.get("PREP_KIT_IN", "input.csv")
IN_FORMAT = os.environ.get("PREP_KIT_IN_FORMAT", "CSV").upper()
OUT_PATH = os.environ.get("PREP_KIT_OUT", "output.csv")


def load_data() -> pd.DataFrame:
    """This stage's input dataset (the collected data for cleaning, the
    cleaned data for enrichment), freshly read on every call."""
    if IN_FORMAT in {"XLSX", "XLS"}:
        return pd.read_excel(IN_PATH)
    if IN_FORMAT == "JSON":
        return pd.read_json(IN_PATH)
    return pd.read_csv(IN_PATH, sep=None, engine="python", encoding="utf-8-sig")


def save_data(df: pd.DataFrame) -> int:
    """Save the prepared DataFrame as this stage's output (replacing any
    earlier save in the same run). Returns the number of rows saved."""
    if not isinstance(df, pd.DataFrame):
        raise TypeError(f"save_data expects a pandas DataFrame, got {type(df).__name__}")
    df.to_csv(OUT_PATH, index=False)
    return len(df)

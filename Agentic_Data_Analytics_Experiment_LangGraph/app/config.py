"""Every setting a run depends on: how long it may take, how many turns each
phase gets, where its files live, and which dataset is the last-resort
fallback. Change the demo's pacing here, nowhere else.
"""

from pathlib import Path

# --- Timing & turn budgets ----------------------------------------------------

# The one knob for how long a full run should take, wall-clock — everything
# below is derived from it, scaled relative to the tuned 20-minute baseline
# (set this to 20 to get exactly the original, hand-tuned numbers back).
DEMO_LENGTH_MINUTES = 60
_SCALE = DEMO_LENGTH_MINUTES / 20

TURN_DELAY_SECONDS = 4  # pace the conversation so a class can read along;
# NOT scaled — a longer run should mean more real turns, not more waiting.

# No-tool discussion phases have nothing real to anchor to yet, so kept
# short — left running long, they tend to invent increasingly elaborate
# fictional detail (a different database system, timelines, etc.) instead of
# staying grounded in what the real tools actually do. So these scale much
# more mildly than the tool-backed budgets below, on purpose: a 3x longer
# demo should mean far more real scraping/searching, not 3x more invented
# small talk.
_DISCUSSION_SCALE = 1 + (_SCALE - 1) * 0.3
MIN_TURNS_DISCUSSION = round(2 * _DISCUSSION_SCALE)
MAX_TURNS_DISCUSSION = round(6 * _DISCUSSION_SCALE)
# The 3-way round-robins (Step 1's "other objectives" / privacy checks) need
# at least one turn per peer, however short the demo is set to.
MIN_TURNS_ROUND_ROBIN = max(3, round(3 * _DISCUSSION_SCALE))

# Tool-backed action phases get more room since real results keep grounding
# each turn — these scale with the full length.
MIN_TURNS_ACTION = round(4 * _SCALE)
MAX_TURNS_ACTION = round(14 * _SCALE)
# Collecting data (step 3) must now keep trying different searches until it
# finds genuine listing-level data rather than settling for an aggregate, so
# it gets extra room beyond the normal action budget above — and since the
# Data Engineer now takes a third of the round-robin's turns there too
# (see agents/personas.py's data_engineer_collecting), the budget is scaled
# up so the Data Analyst still gets roughly as many actual
# searching/downloading turns as before.
MAX_TURNS_COLLECT = round(36 * _SCALE)
MAX_TURNS = round(80 * _SCALE)  # safety net: a live demo shouldn't run forever if nobody stops it
MAX_RUNTIME_SECONDS = DEMO_LENGTH_MINUTES * 60  # ...or this many minutes, whichever comes first

# --- Paths --------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent  # the app root, one level above app/
STATIC_DIR = BASE_DIR / "static"
# Every real dataset file a run touches (downloaded, cleaned, the SQLite
# database, the fallback dataset) lives here — kept separate from the
# source files.
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DOWNLOAD_PATH = DATA_DIR / "downloaded_dataset.csv"
SCRAPED_PATH = DATA_DIR / "scraped_listings.csv"
# Every scraper version the agents write (scraper_vN.py) and each run's
# working folder (request log, printed output, raw CSV).
SCRAPERS_DIR = DATA_DIR / "scrapers"
CLEANED_PATH = DATA_DIR / "cleaned_dataset.csv"
DB_PATH = DATA_DIR / "rental_data.db"
FALLBACK_PATH = DATA_DIR / "fallback_dataset.csv"
# Every run's full agent-to-agent conversation is saved here as
# Markdown + HTML once the run ends, for later review.
CONVERSATION_HISTORY_DIR = BASE_DIR / "conversation_history"

# --- Fallback dataset -----------------------------------------------------------

# The guaranteed last-resort dataset if Step 3 never confirms an
# individual-apartment-level dataset within the search budget — a real
# Statistik Stadt Zürich rent survey. It's aggregated, not individual-level,
# but real and always available, so Step 4 still has something genuine to
# clean/store/query rather than the run ending with nothing.
FALLBACK_DATASET_URL = (
    "https://data.stadt-zuerich.ch/dataset/bau_whg_mpe_mietpreis_raum_zizahl_gn_jahr_od5161"
    "/download/BAU516OD5161.csv"
)
FALLBACK_DATASET_FORMAT = "CSV"
FALLBACK_DATASET_TITLE = "Mietpreise in der Stadt Zürich (MPE Abfragetool)"
FALLBACK_DATASET_ORGANIZATION = "Statistik Stadt Zürich"
FALLBACK_DATASET_PAGE_URL = (
    "https://data.stadt-zuerich.ch/dataset/bau_whg_mpe_mietpreis_raum_zizahl_gn_jahr_od5161"
)

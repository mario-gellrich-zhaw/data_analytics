"""Every tool schema the models see, in one place — what each agent is
told a tool does and which arguments it takes. The Python that really runs
behind each name is in opendata.py, preparation.py, scraper.py and
prep_code.py, wired
up per run in run_tools.py; which agent gets which schemas is decided in
agents/personas.py.
"""

from sandbox.scraper_kit import ALLOWED_DOMAINS, FIELDS
from tools import prep_code
from tools.scraper import MAX_REQUESTS_PER_RUN, MAX_ROWS, RUN_TIMEOUT_SECONDS

# --- Data Analyst: Collecting data -----------------------------------------

WRITE_SCRAPER_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_scraper_code",
        "description": (
            "Write (or rewrite) your own complete Python scraper script. It is "
            "checked and saved as a new version; then call run_scraper to really "
            "run it. Rules, enforced in code: the script may import only "
            "scraper_kit, bs4, json, re, math, time, datetime, collections, "
            "itertools, functools, statistics, string, html, unicodedata, typing, "
            "dataclasses, urllib.parse — no requests/urllib.request/socket/os/"
            "subprocess, no open/eval/exec/getattr, no _private attributes. The ONLY "
            "way to the web is scraper_kit.polite_get(url, params=None) -> Page "
            "(.status_code, .text, .headers, .json()); it checks robots.txt, allows "
            f"only {', '.join(ALLOWED_DOMAINS)}, waits 2-5 s between "
            f"requests, allows at most {MAX_REQUESTS_PER_RUN} requests per run, and "
            "raises scraper_kit.ScrapeBlocked at the first 403/429/bot challenge "
            "(that site then stays blocked for the run — never try to get around "
            "it). Store results with scraper_kit.save_rows(list_of_dicts) using only "
            f"these keys: {', '.join(FIELDS)} (at most {MAX_ROWS} rows kept) — call "
            "it after EVERY page, not once at the end: rows already saved are kept "
            "even if the script stops or crashes later. "
            "Keep only rental apartments — listing sites also carry parking "
            "spaces, commercial units and properties for sale. "
            "print() anything useful — you'll "
            "see the output; in particular print, per page, how many items each "
            "of your filters dropped, so a filter that silently drops everything "
            "(a wrong key name reads as None) shows up at once. Known entry points: "
            "immoscout24.ch search "
            "https://www.immoscout24.ch/de/wohnung/mieten/ort-zuerich?pn=1 ; "
            "homegate.ch search https://www.homegate.ch/mieten/wohnung/ort-zuerich/"
            "trefferliste ; flatfox.ch public JSON API "
            "https://flatfox.ch/api/v1/public-listing/ (paginated with limit (max "
            "100) and offset, returns {count, next, results: [...]}, all of "
            "Switzerland, no server-side location filter or sorting). Flatfox "
            "results are OLDEST first — the first pages are years-old leftovers, "
            "mostly parking spaces — and the current listings are at the END: make "
            "one request with limit=1 just to read count, then page backwards from "
            "offset=count-100 (count-200, count-300, ...). Flatfox's real item keys "
            "(don't guess others): pk (listing id), offer_type ('RENT'/'SALE'), "
            "object_category ('APARTMENT', 'SHARED', 'PARK', 'HOUSE', 'INDUSTRY', "
            "...), rent_gross / rent_net / rent_charges (can all be null = price on "
            "request, skip those), number_of_rooms (a string like '3.5'), "
            "surface_living, floor, year_built, year_renovated, street, zipcode "
            "(int), city, latitude, longitude, url (relative, prefix "
            "https://flatfox.ch), object_type (e.g. 'APARTMENT', 'ATTIC_FLAT'), "
            "description (the listing's free text — save it: the next steps derive "
            "features from it), attributes (a list like [{'name': 'balcony'}, "
            "{'name': 'lift'}] — save the names comma-joined), is_furnished, "
            "moving_date. Never save the agency/contact fields (personal data). The "
            "canton field (state) is usually empty, so filter to Zurich by "
            "zipcode 8000-8999. Catch ScrapeBlocked per "
            "site so one blocked site doesn't stop the whole script."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The complete Python script (not a diff).",
                }
            },
            "required": ["code"],
        },
    },
}

RUN_SCRAPER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_scraper",
        "description": (
            "Really run a scraper version you wrote with write_scraper_code, in a "
            f"separate process (time limit {RUN_TIMEOUT_SECONDS} s). Returns the "
            "real exit code, the tail of its printed output (incl. any traceback), "
            "every request it made with its real HTTP status or block reason, the "
            "real structure of the first page fetched per site (response_structure: "
            "JSON keys, or the HTML title) and how many rows it saved plus a sample. "
            "If it failed, read the error and response_structure — use the real key "
            "names shown there, don't guess — fix the code with write_scraper_code, "
            "and run again."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "version": {
                    "type": "integer",
                    "description": "Which version to run; omit for the latest.",
                }
            },
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
                "resource_id": {
                    "type": "string",
                    "description": (
                        "The resource's id, exactly as returned by search_open_data (e.g. 'r0')."
                    ),
                },
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
            "searching for a replacement — never keep or reuse a file you've identified "
            "as unsuitable."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {
                    "type": "string",
                    "description": (
                        "Short real reason it's being discarded, e.g. 'aggregated by "
                        "municipality' or 'not rental data, it's museum exhibitions'."
                    ),
                }
            },
            "required": ["reason"],
        },
    },
}


# --- Data Engineer: Preparing & storing data --------------------------------

PREVIEW_DATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "preview_data",
        "description": (
            "Really read and return the first N rows of the downloaded data file, when asked "
            "to show what the data actually looks like."
        ),
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

# --- Data Engineer (cleaning) / Data Analyst (enrichment) --------------------

WRITE_PREP_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_prep_code",
        "description": (
            "Write (or rewrite) your own complete Python data-preparation script "
            "for the CURRENT stage (cleaning, or enrichment). It is checked and "
            "saved as a new version; then call run_prep_code to really run it. "
            "Read the input with df = prep_kit.load_data() (cleaning: the collected "
            "listings; enrichment: the cleaned listings) and hand back the result "
            "with prep_kit.save_data(df). Use pandas (import pandas as pd) and "
            "numpy freely for the transformation itself — you decide what the data "
            "needs. Rules, enforced in code: the script may import only prep_kit, "
            "scraper_kit, pandas, numpy, json, re, math, time, datetime, "
            "collections, itertools, functools, statistics, string, html, "
            "unicodedata, typing, dataclasses, urllib.parse; no file or URL access "
            "of its own (no open, no pd.read_*/df.to_*, no eval/query/getattr, no "
            "_private attributes). The ONLY way to the web is "
            "scraper_kit.polite_get(url, params=None) -> Page (.text, .json()), "
            f"allowed for {', '.join(prep_code.PREP_DOMAINS)} only, robots.txt "
            f"checked, at most {prep_code.MAX_REQUESTS_PER_RUN} requests per run, "
            "0.2-0.5 s apart for api3.geo.admin.ch and 2-5 s apart for flatfox.ch; "
            "it raises scraper_kit.ScrapeBlocked at the first 403/429 (never try to "
            "get around it — catch it and keep what you have); a 400/404 fails only "
            "that request and its message quotes the server's own explanation — "
            "print it. Known entry point: "
            "the Swiss federal geodata API identify service "
            "https://api3.geo.admin.ch/rest/services/api/MapServer/identify with "
            "params geometry='<lon>,<lat>', geometryType='esriGeometryPoint', "
            "sr=4326, tolerance=0, returnGeometry='false', layers='all:<layer id>' "
            "— e.g. layer "
            "ch.swisstopo.swissboundaries3d-gemeinde-flaeche.fill (the municipality "
            "a point lies in; add timeInstant=<year>, else you get every historic "
            "boundary version) or ch.bfs.gebaeude_wohnungs_register (the federal "
            "building register; needs tolerance=<pixels> > 0, "
            "mapExtent='<minlon>,<minlat>,<maxlon>,<maxlat>' and "
            "imageDisplay='<w>,<h>,96'). Read the real key names from "
            "response_structure after a first small run instead of guessing. "
            "print() what each step does (e.g. how many rows a filter drops, how "
            "many lookups succeeded) — you'll see the output. Rules checked on "
            "every run: keep listing_id and one row per listing; cleaning may not "
            f"drop more than {prep_code.MAX_DROPPED_SHARE:.0%} of the rows; "
            "enrichment must keep every row and add at least one column. Time "
            f"limit {prep_code.RUN_TIMEOUT_SECONDS} s per run."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The complete Python script (not a diff).",
                }
            },
            "required": ["code"],
        },
    },
}

RUN_PREP_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_prep_code",
        "description": (
            "Really run a preparation script you wrote with write_prep_code, in a "
            "separate process. Returns the real exit code, the tail of its printed "
            "output (incl. any traceback), rows before/after, which columns were "
            "added/removed, the output's dtypes and missing values per column, a "
            "sample of rows, any web requests it made (with the real response "
            "structure), and whether the result was accepted as the current "
            "dataset (or why not). If it failed or was rejected, fix the code and "
            "run again."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "version": {
                    "type": "integer",
                    "description": "Which version to run; omit for the latest.",
                }
            },
        },
    },
}

STORE_TO_DATABASE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "store_to_database",
        "description": (
            "Really write the current prepared (cleaned and enriched) file into a "
            "real local SQLite database table."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "Table name to store the data in, e.g. 'apartments'.",
                },
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


# --- Teaching aids: real examples for the class, help when stuck ---------

SHOW_TO_CLASS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "show_to_class",
        "description": (
            "Put a REAL example in front of the students watching this demo, built "
            "from the actual files (you only choose what to show): "
            "kind='single_case' shows ONE listing field by field, as collected vs. "
            "now — e.g. its description text next to the values your code derived "
            "from it (give listing_id and the columns to compare); kind='rows' "
            "shows a few real rows of the current dataset (columns, n <= 8, "
            "optionally listing_ids); kind='code' shows a few real lines of a "
            "script written this run (script like 'enrich_v2.py', start_line, "
            "end_line; <= 40 lines). Always add a one-sentence caption saying what "
            "to notice. Use it once or twice per step for what's worth learning "
            "from, not every turn."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["single_case", "rows", "code"]},
                "caption": {
                    "type": "string",
                    "description": "One sentence: what the students should notice.",
                },
                "listing_id": {"type": "string", "description": "single_case: which listing."},
                "columns": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "single_case/rows: which columns (real names) to show.",
                },
                "n": {"type": "integer", "description": "rows: how many rows (max 8)."},
                "listing_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": "rows: show only these listings.",
                },
                "script": {"type": "string", "description": "code: file name, e.g. 'clean_v1.py'."},
                "start_line": {"type": "integer", "description": "code: first line to show."},
                "end_line": {"type": "integer", "description": "code: last line to show."},
            },
            "required": ["kind", "caption"],
        },
    },
}

LOOK_UP_PAST_RUNS_SCHEMA = {
    "type": "function",
    "function": {
        "name": "look_up_past_runs",
        "description": (
            "ONLY when you're stuck (your last runs in this step all failed): look at "
            "how earlier runs of this demo solved the same step, from their saved "
            "conversation history — the script that finally worked and the problems "
            "they hit. Refused before you're stuck: first find your own way."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "stage": {
                    "type": "string",
                    "enum": ["scraper", "clean", "enrich"],
                    "description": "Which step; omit for the current one.",
                },
                "keyword": {
                    "type": "string",
                    "description": "Optional: only scripts/problems mentioning this.",
                },
            },
        },
    },
}


# --- All agents: optional sketches ------------------------------------------

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
                "kind": {
                    "type": "string",
                    "enum": ["ascii", "dot"],
                    "description": "Whether content is plain ASCII art or Graphviz DOT source.",
                },
                "title": {"type": "string", "description": "A short title for the sketch."},
                "content": {"type": "string", "description": "The ASCII art or DOT source itself."},
            },
            "required": ["kind", "content"],
        },
    },
}

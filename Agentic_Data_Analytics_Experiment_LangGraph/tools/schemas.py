"""Every tool schema the models see, in one place — what each agent is
told a tool does and which arguments it takes. The Python that really runs
behind each name is in opendata.py, preparation.py and scraper.py, wired
up per run in run_tools.py; which agent gets which schemas is decided in
agents/personas.py.
"""

from sandbox.scraper_kit import ALLOWED_DOMAINS, FIELDS
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
            "see the output. Known entry points: immoscout24.ch search "
            "https://www.immoscout24.ch/de/wohnung/mieten/ort-zuerich?pn=1 ; "
            "homegate.ch search https://www.homegate.ch/mieten/wohnung/ort-zuerich/"
            "trefferliste ; flatfox.ch public JSON API "
            "https://flatfox.ch/api/v1/public-listing/ (paginated with limit (max "
            "100) and offset, returns {count, next, results: [...]}, all of "
            "Switzerland, no server-side location filter or sorting; results are "
            "oldest first and the first pages contain very few Zurich listings, so "
            "spread your offsets across the whole range up to count). Catch ScrapeBlocked per "
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

CLEAN_DATA_SCHEMA = {
    "type": "function",
    "function": {
        "name": "clean_data",
        "description": (
            "Really clean the downloaded file: drop exact duplicate rows and/or "
            "rows missing values in key columns you name (based on what "
            "profile_data showed). Writes a real cleaned file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "drop_duplicates": {
                    "type": "boolean",
                    "description": "Drop exact duplicate rows. Default true.",
                },
                "drop_missing_in": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Column names to require non-missing values in (rows missing any of "
                        "these are dropped). Use real column names from profile_data."
                    ),
                },
            },
            "required": [],
        },
    },
}

STORE_TO_DATABASE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "store_to_database",
        "description": "Really write the cleaned file into a real local SQLite database table.",
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

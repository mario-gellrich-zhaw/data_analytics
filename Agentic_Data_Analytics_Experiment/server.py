"""Tiny web backend for the agentic demo (FastAPI, not Flask).

Serves a single static page and one Server-Sent-Events endpoint that walks
three peer agents — a Product Manager, a Data Analyst, and a Data Engineer,
none of them ranking above the others — through the first four steps of the
course's Data Analytics Process Model, in order, once:

  1. Business objective    — fixed (given, not agent-decided)
  2. Defining appropriate data — discussion only (no tools yet)
  3. Collecting data        — real scraping/search/download tools
  4. Preparing & storing data — discussion, then real cleaning + real
                                 SQLite storage + a real SQL query

Deliberately no analysis/interpretation happens here (that's the next,
not-yet-built part of the process model) — step 4's tools profile and clean
data structurally, they don't derive insights. Each step is a bounded
conversation between the Product Manager and whichever specialist (or
specialists) own that step — usually 2-party, but step 1's "other
objectives" aside and step 3's collection both bring all three peers in,
the latter with the Data Engineer weighing in on ingestion/pipeline
concerns while the Data Analyst keeps sole ownership of the actual
scraping/search/download tools — that ends once everyone says, via a
status tag, that it's genuinely done; the whole run ends after step 4 —
there's no open-ended looping afterward.

A manual Stop (POST /api/stop) ends the run cleanly at any point, and a
generous safety net (max turns / max wall-clock time) bounds it regardless.
"""

import json
import queue
import random
import re
import threading
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from openai import OpenAI

from agents import Agent
from data_tool import (
    ATTEMPT_SCRAPE_SCHEMA,
    CLEAN_DATA_SCHEMA,
    DISCARD_DATASET_SCHEMA,
    DOWNLOAD_DATASET_SCHEMA,
    MAKE_SKETCH_SCHEMA,
    PREVIEW_DATA_SCHEMA,
    PROFILE_DATA_SCHEMA,
    RUN_SQL_QUERY_SCHEMA,
    SEARCH_OPEN_DATA_SCHEMA,
    STORE_TO_DATABASE_SCHEMA,
    attempt_scrape,
    clean_data,
    discard_dataset,
    download_dataset,
    make_sketch,
    preview_data,
    profile_data,
    run_sql_query,
    search_open_data,
    store_to_database,
)

load_dotenv()  # finds the .env at the repo root
client = OpenAI()

MODEL = "gpt-4o-mini"
TURN_DELAY_SECONDS = 4  # pace the conversation so a class can read along
# No-tool discussion phases have nothing real to anchor to yet, so kept
# short — left running long, they tend to invent increasingly elaborate
# fictional detail (a different database system, timelines, etc.) instead of
# staying grounded in what the real tools actually do. Tool-backed action
# phases get more room since real results keep grounding each turn.
MIN_TURNS_DISCUSSION = 2
MAX_TURNS_DISCUSSION = 6
MIN_TURNS_ACTION = 4
MAX_TURNS_ACTION = 14
# Collecting data (step 3) must now keep trying different searches until it
# finds genuine listing-level data rather than settling for an aggregate, so
# it gets extra room beyond the normal action budget above — and since the
# Data Engineer now takes a third of the round-robin's turns there too
# (see data_engineer_collecting), the budget is scaled up so the Data
# Analyst still gets roughly as many actual searching/downloading turns as
# before.
MAX_TURNS_COLLECT = 36
MAX_TURNS = 80  # safety net: a live demo shouldn't run forever if nobody stops it
MAX_RUNTIME_SECONDS = 20 * 60  # ...or 20 minutes, whichever comes first
STATUS_TAG_RE = re.compile(r"\s*\[STATUS:\s*(CONTINUE|NEXT)\]\s*$", re.IGNORECASE)
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
# The model occasionally mimics the "Speaker: text" formatting it sees for
# the *other* agent's turns (see agents.py's _messages_for) and mistakenly
# prefixes its OWN reply with a name — including sometimes the wrong one.
# Once that lands in the shared transcript, every future turn sees it as
# "how this agent talks" and the pattern self-reinforces. Stripped here,
# mechanically, before anything is stored — no persona wording alone proved
# reliable enough to prevent it.
SPEAKER_PREFIX_RE = re.compile(
    r"^(Product Manager|Data Analyst|Data Engineer)\s*:\s*", re.IGNORECASE
)

STATUS_TAG_INSTRUCTION = (
    " End every message on a new line with exactly '[STATUS: CONTINUE]' if "
    "there's more to do for the CURRENT step, or '[STATUS: NEXT]' once you "
    "think it's genuinely done. Never prefix your message with a name or "
    "role label (e.g. don't start with 'Data Engineer:' or 'Data Analyst:') "
    "— just write your reply directly, the UI already shows who's speaking."
)

BE_CONCISE = (
    " Talk like a real colleague in a quick chat, not a report. AT MOST 2 "
    "short sentences, one paragraph — if a third sentence would help, cut "
    "something instead of adding it. Get straight to the point: no opening "
    "filler ('Absolutely, great question!', 'Sure! Here's a breakdown...', "
    "'That looks solid!') and no closing filler either — don't wrap up with "
    "a sentence that just restates what you said, or a vague forward-look "
    "like 'let's keep this in mind' or 'this aligns well with our goals'; "
    "stop right after the actual content. Don't default to bullet lists: "
    "most messages should just be plain conversational text. Only switch to "
    "a short bullet list or tiny table when you're genuinely comparing "
    "several distinct items AND prose would be more awkward than a list — "
    "and even then just name the items, one line each; don't add a "
    "type/format/definition explanation per item unless the goal you were "
    "just given specifically asks for a schema or data-type breakdown. If "
    "there are many possible items, don't enumerate them all: mention a few "
    "naturally instead, e.g. 'we could use data such as the FSO price index "
    "or ImmoScout24 listings' rather than listing every option. Still sound "
    "like a real person talking, not a checklist."
)

# Varied so a class watching several runs back-to-back doesn't hear the
# exact same opening line every time — the substance after the opener stays
# the same, only the greeting/framing changes.
BUSINESS_OBJECTIVE_OPENERS = [
    "Welcome, everyone!",
    "Hi team, thanks for joining.",
    "Morning, everyone — let's get started.",
    "Good to have you all here.",
    "Alright team, let's dive in.",
]


def _build_business_objective() -> str:
    opener = random.choice(BUSINESS_OBJECTIVE_OPENERS)
    return (
        f"{opener} Our goal for this project is to build a price-prediction "
        "model for rental apartments in the canton of Zurich. The "
        "deliverable is a model that estimates a fair market rent for a "
        "given apartment from real features like size, room count, and "
        "location — useful for tenants sanity-checking an asking price and "
        "for landlords pricing a listing. To get there, we'll follow our "
        "data analytics process model, starting with figuring out what "
        "data we actually need."
    )


BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOAD_PATH = BASE_DIR / "downloaded_dataset.csv"
CLEANED_PATH = BASE_DIR / "cleaned_dataset.csv"
DB_PATH = BASE_DIR / "rental_data.db"
FALLBACK_PATH = BASE_DIR / "fallback_dataset.csv"
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
# Every run's full agent-to-agent conversation is saved here as a
# human-readable Markdown file once the run ends, for later review.
CONVERSATION_HISTORY_DIR = BASE_DIR / "conversation_history"

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# One demo at a time (this is a single-instructor classroom tool, not a
# multi-user service) — a plain module-level flag is enough to let the
# frontend's Stop button end an in-progress run.
stop_event = threading.Event()


@app.get("/")
def index():
    """Serve the single static demo page."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Avoid noisy 404s from browsers auto-requesting /favicon.ico."""
    return Response(status_code=204)


@app.post("/api/stop")
def stop():
    """Signal an in-progress run to end cleanly at its next checkpoint."""
    stop_event.set()
    return {"stopping": True}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


_OUTCOME_HEADLINES = {
    "completed": "✅ Completed all 4 steps",
    "completed_with_fallback": "⚠️ Completed with a fallback dataset (not individual-level)",
    "incomplete": "⚠️ Incomplete — no individual-apartment-level dataset confirmed",
    "stopped": "⏹️ Stopped by the user",
    "timed_out": "⏱️ Hit the safety net before finishing",
    "error": "❌ Errored",
    "unknown": "❔ Unknown",
}


def _objective_headline(business_objective: str) -> str:
    # The opener varies run to run (see BUSINESS_OBJECTIVE_OPENERS); the
    # actual goal sentence that follows it doesn't, so anchor on that
    # instead of assuming a fixed prefix length.
    marker = "Our goal"
    idx = business_objective.find(marker)
    if idx == -1:
        return business_objective.split(". ")[0].strip()
    remainder = business_objective[idx:]
    end = remainder.find(". ")
    return (remainder[: end + 1] if end != -1 else remainder).strip()


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _render_phase_result_lines(step: int, data: dict) -> list[str]:
    """Compact, human-facing facts behind a step's tools — the same fields
    static/app.js's buildCollectingCard/buildPreparingCard already surface,
    so the saved file and the live UI agree on what matters."""
    lines: list[str] = []
    if step == 3:
        opendata = data.get("opendata") or {}
        if opendata.get("query"):
            lines.append(
                f"- Search: \"{opendata['query']}\" → {opendata.get('total_found', 0)} "
                "candidate(s) found on opendata.swiss."
            )
        download = data.get("download") or {}
        if download.get("success"):
            title = download.get("dataset_title")
            if title:
                org = download.get("dataset_organization") or "opendata.swiss"
                url = download.get("dataset_url")
                source = f"[{title}]({url})" if url else title
                lines.append(f"- Downloaded: {source} ({org}).")
            bytes_ = download.get("bytes")
            if bytes_ is not None:
                lines.append(f"- {bytes_:,} bytes saved ({download.get('format', 'file')}).")
        elif download:
            lines.append(f"- Download failed: {download.get('error', 'unknown error')}")
        preview = data.get("preview") or {}
        if preview.get("columns"):
            n_rows = len(preview.get("rows") or [])
            lines.append(
                f"- Preview: {n_rows} rows, columns: {', '.join(map(str, preview['columns']))}."
            )
    elif step == 4:
        profile = data.get("profile") or {}
        if "n_rows" in profile:
            n_missing_cols = len(profile.get("missing_values") or {})
            lines.append(
                f"- Profiled: {profile['n_rows']:,} rows, {profile.get('n_columns')} columns, "
                f"{profile.get('duplicate_rows', 0):,} duplicate rows, {n_missing_cols} "
                "columns with missing values."
            )
        clean = data.get("clean") or {}
        if "rows_after" in clean:
            lines.append(
                f"- Cleaned: {clean.get('rows_before', 0):,} → {clean.get('rows_after', 0):,} rows "
                f"({clean.get('dropped_duplicates', 0):,} duplicates, "
                f"{clean.get('dropped_missing', 0):,} missing-value rows dropped)."
            )
        store = data.get("store") or {}
        if store.get("table_name"):
            lines.append(
                f"- Stored {store.get('rows_stored', 0):,} rows in table "
                f"\"{store['table_name']}\" ({store.get('db_bytes', 0):,} bytes)."
            )
        sql = data.get("sql") or {}
        if sql.get("query"):
            n_rows = len(sql.get("rows") or [])
            lines.append(f"- Verification query: `{sql['query']}` → {n_rows} row(s) returned.")
        sketch = data.get("sketch") or {}
        if sketch.get("title"):
            lines.append(f"- Sketch: \"{sketch['title']}\" ({sketch.get('kind', 'ascii')}).")
    return lines


def _render_conversation_history(
    history: list[dict], started_at: datetime, ended_at: datetime, outcome: dict
) -> str:
    """Render one run's recorded phases/turns as a human-readable Markdown transcript."""
    turn_count = sum(1 for entry in history if entry["kind"] == "turn")
    objective = next((e["goal"] for e in history if e["kind"] == "phase"), "")
    status = outcome.get("status", "unknown")

    lines = [
        "# Agentic Conversation — Data Analytics Process Model",
        f"**Run started:** {started_at:%Y-%m-%d %H:%M:%S}",
        f"**Outcome:** {_OUTCOME_HEADLINES.get(status, status)}",
        f"**Turns:** {turn_count} over {_format_duration((ended_at - started_at).total_seconds())}",
    ]
    if objective:
        lines.append(f"**Objective:** {_objective_headline(objective)}")
    lines.append("")

    for entry in history:
        if entry["kind"] == "phase":
            header = f"## Step {entry['step']}/4 · {entry['step_label']}"
            if entry["sub_label"]:
                header += f" — {entry['sub_label']}"
            lines.append(header)
            lines.append(f"*Goal: {entry['goal']}*")
            lines.append("")
        elif entry["kind"] == "turn":
            suffix = " _(used a real tool)_" if entry["action"] else ""
            lines.append(f"**{entry['speaker']}:** {entry['text']}{suffix}")
            lines.append("")
        elif entry["kind"] == "phase_result":
            result_lines = _render_phase_result_lines(entry["step"], entry["data"])
            if result_lines:
                lines.append("**Real results:**")
                lines.extend(result_lines)
                lines.append("")

    lines.append("## Run ended")
    lines.append(f"**Status:** {_OUTCOME_HEADLINES.get(status, status)}")
    if outcome.get("message"):
        lines.append(outcome["message"])

    return "\n".join(lines).rstrip() + "\n"


def _save_conversation_history(history: list[dict], started_at: datetime, outcome: dict) -> None:
    # Best-effort only: a failure to save the transcript should never break
    # the live demo or leave the SSE stream hanging.
    if not history:
        return
    try:
        ended_at = datetime.now()
        CONVERSATION_HISTORY_DIR.mkdir(exist_ok=True)
        filename = f"conversation_{started_at:%Y%m%d_%H%M%S}.md"
        (CONVERSATION_HISTORY_DIR / filename).write_text(
            _render_conversation_history(history, started_at, ended_at, outcome), encoding="utf-8"
        )
    except OSError:
        pass


def _run_demo(q: "queue.Queue"):
    run_started_at = datetime.now()
    history: list[dict] = []  # every phase header and turn, saved to disk once the run ends
    # Overwritten below on every real exit path; this default only matters
    # if an exception somehow slips past the except block untouched.
    outcome: dict = {"status": "unknown"}
    try:

        def on_progress(stage: str):
            q.put(_sse("progress", {"stage": stage}))

        # Each run starts genuinely fresh — no file from a previous run can
        # leak in and be mistaken for real data in this one.
        for stale_path in (DOWNLOAD_PATH, CLEANED_PATH, DB_PATH, FALLBACK_PATH):
            stale_path.unlink(missing_ok=True)

        # --- shared state written by tool calls, read for phase-end cards ---
        opendata_result = {}
        download_result = {}
        download_preview_result = (
            {}
        )  # first 10 raw rows, captured automatically right after download
        profile_result = {}
        clean_result = {}
        clean_preview_result = (
            {}
        )  # first 10 cleaned rows, captured automatically right after cleaning
        store_result = {}
        sql_result = {}
        sketch_result = {}
        resource_lookup = {}  # short id -> real resource info, from search_open_data
        current_file = {
            "path": str(DOWNLOAD_PATH),
            "format": "CSV",
        }  # what preview/profile/clean act on right now
        dataset_ready = {
            "value": False
        }  # True only while current_file points at a real, undiscarded download

        def capture_preview(target: dict, path: str, data_format: str):
            # A real preview of the first 10 rows, captured automatically the
            # moment data becomes available (after download, after cleaning)
            # — shown in the UI regardless of whether an agent happens to
            # call preview_data itself.
            try:
                result = preview_data(path=path, data_format=data_format, n=10)
                target.clear()
                target.update(result)
            except Exception:
                pass  # best-effort only; never break the run over a preview

        min_listing_rows = 15  # a whole-canton listings dataset should clear this easily

        def check_looks_like_listing_data(path: str, data_format: str) -> tuple[bool, str]:
            # A real, code-level structural sanity check that runs on every
            # download regardless of whether the agent itself remembers to
            # call preview_data — prompting alone proved unreliable here
            # (the model sometimes skipped its own verification step and
            # declared success on an obviously aggregated file). This can't
            # judge topic/semantics, but it reliably catches the two most
            # common real failure shapes seen live: multi-header statistics
            # exports (mostly "Unnamed: N" columns after pandas parses them),
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
            if n_rows < min_listing_rows:
                return False, (
                    f"only {n_rows} rows — far too few to be individual apartment listings for "
                    "the canton of Zurich, this looks like a small summary/pivot table"
                )
            return True, ""

        # --- Data Analyst tools: Collecting data --------------------------

        def call_attempt_scrape(site: str):
            return attempt_scrape(site=site, on_progress=on_progress)

        def call_search_open_data(query: str):
            result = search_open_data(query=query, on_progress=on_progress)
            opendata_result.clear()
            opendata_result.update(result)

            # Short, stable resource ids instead of making the model retype a
            # long URL to pick one (see data_tool.py's search_open_data docs).
            agent_view_datasets = []
            for ds in result["datasets"]:
                resource_views = []
                for res in ds["resources"]:
                    rid = f"r{len(resource_lookup)}"
                    resource_lookup[rid] = {
                        "url": res["url"],
                        "format": res["format"],
                        "dataset_title": ds["title"],
                        "dataset_organization": ds["organization"],
                        "dataset_url": ds["url"],
                    }
                    resource_views.append({"id": rid, "format": res["format"]})
                agent_view_datasets.append(
                    {
                        "title": ds["title"],
                        "organization": ds["organization"],
                        "url": ds["url"],
                        "resources": resource_views,
                    }
                )
            response = {
                "query": result["query"],
                "total_found": result["total_found"],
                "datasets": agent_view_datasets,
            }
            if result.get("error"):
                response["error"] = result["error"]
            return response

        def call_download_dataset(resource_id: str):
            picked = resource_lookup.get(resource_id)
            if not picked:
                return {
                    "success": False,
                    "error": (
                        f"Unknown resource_id '{resource_id}'. Use an id exactly as "
                        "returned by search_open_data."
                    ),
                }

            result = download_dataset(
                resource_url=picked["url"],
                resource_format=picked["format"],
                out_path=str(DOWNLOAD_PATH),
                on_progress=on_progress,
            )

            if result.get("success"):
                looks_ok, reason = check_looks_like_listing_data(
                    str(DOWNLOAD_PATH), picked["format"]
                )
                if not looks_ok:
                    # Automatically recognized as unsuitable (aggregated /
                    # not listing-level) — really delete it and report the
                    # rejection as the actual tool result, regardless of
                    # what the agent itself would have concluded.
                    Path(DOWNLOAD_PATH).unlink(missing_ok=True)
                    result["success"] = False
                    result["error"] = (
                        f"Downloaded, but automatically rejected: {reason}. Try a "
                        "different dataset."
                    )
                    current_file["path"] = ""
                    current_file["format"] = ""
                    dataset_ready["value"] = False
                    download_preview_result.clear()
                else:
                    current_file["path"] = str(DOWNLOAD_PATH)
                    current_file["format"] = picked["format"]
                    dataset_ready["value"] = True
                    capture_preview(
                        download_preview_result, current_file["path"], current_file["format"]
                    )
            else:
                # A failed attempt must never leave an earlier, unrelated
                # download's preview looking like it belongs to this result.
                download_preview_result.clear()

            download_result.clear()
            download_result.update(result)
            download_result["resource_url"] = picked["url"]
            download_result["dataset_title"] = picked["dataset_title"]
            download_result["dataset_organization"] = picked["dataset_organization"]
            download_result["dataset_url"] = picked["dataset_url"]
            return result

        def call_discard_dataset(reason: str = ""):
            result = discard_dataset(
                path=current_file["path"], reason=reason, on_progress=on_progress
            )
            download_result.clear()
            download_preview_result.clear()
            current_file["path"] = ""
            current_file["format"] = ""
            dataset_ready["value"] = False
            return result

        # --- Data Engineer tools: Preparing & storing data -----------------

        def _no_file_error() -> dict | None:
            # A tool the model calls before any successful download (or
            # after a discard_dataset) has nothing real to read — report
            # that plainly instead of letting a raw FileNotFoundError crash
            # the whole run.
            if not current_file["path"] or not Path(current_file["path"]).exists():
                return {
                    "success": False,
                    "error": "No dataset file is available yet — download (and confirm) one first.",
                }
            return None

        def call_preview_data(n: int = 10):
            err = _no_file_error()
            if err:
                return err
            result = preview_data(
                path=current_file["path"],
                data_format=current_file["format"],
                n=n,
                on_progress=on_progress,
            )
            if result.get("error"):
                return result
            return {"columns": result["columns"], "n_rows_shown": len(result["rows"])}

        def call_profile_data():
            err = _no_file_error()
            if err:
                return err
            result = profile_data(
                path=current_file["path"],
                data_format=current_file["format"],
                on_progress=on_progress,
            )
            if result.get("error"):
                return result
            profile_result.clear()
            profile_result.update(result)
            return result

        def call_clean_data(drop_duplicates: bool = True, drop_missing_in: list | None = None):
            err = _no_file_error()
            if err:
                return err
            result = clean_data(
                source_path=current_file["path"],
                data_format=current_file["format"],
                out_path=str(CLEANED_PATH),
                drop_duplicates=drop_duplicates,
                drop_missing_in=drop_missing_in,
                on_progress=on_progress,
            )
            if result.get("error"):
                return result
            clean_result.clear()
            clean_result.update(result)
            current_file["path"] = str(CLEANED_PATH)
            current_file["format"] = "CSV"
            capture_preview(clean_preview_result, current_file["path"], current_file["format"])
            return result

        def call_store_to_database(table_name: str):
            err = _no_file_error()
            if err:
                return err
            result = store_to_database(
                source_path=current_file["path"],
                db_path=str(DB_PATH),
                table_name=table_name,
                on_progress=on_progress,
            )
            if result.get("error"):
                return result
            store_result.clear()
            store_result.update(result)
            return result

        def call_run_sql_query(query: str):
            result = run_sql_query(query=query, db_path=str(DB_PATH), on_progress=on_progress)
            if result.get("success", True) is not False:
                sql_result.clear()
                sql_result.update(result)
            return result

        # --- All agents: optional sketch ------------------------------------

        def call_make_sketch(kind: str = "ascii", content: str = "", title: str = ""):
            result = make_sketch(content=content, kind=kind, title=title)
            sketch_result.clear()
            sketch_result.update(result)
            return {
                "ok": True
            }  # keep the tool-result text small; the UI reads sketch_result directly

        # --- Agents ----------------------------------------------------------

        product_manager = Agent(
            client=client,
            name="Product Manager",
            persona=(
                "You are the Product Manager on this project. You have no "
                "data tools yourself (you may optionally "
                "use make_sketch to draw a quick ASCII or Graphviz diagram if "
                "it genuinely helps). You and your technical peers (Data "
                "Analyst, Data Engineer) are equals with NO hierarchy — you "
                "don't approve or direct their work, you just ask sharp, "
                "relevant questions for whatever step is currently active. BE "
                "TERSE: one short sentence, max ~12 words, every single "
                "message — no pleasantries, no restating what was just said."
                + STATUS_TAG_INSTRUCTION
            ),
            model=MODEL,
            tools=[MAKE_SKETCH_SCHEMA],
            tool_impls={"make_sketch": call_make_sketch},
        )

        data_analyst_notools = Agent(
            client=client,
            name="Data Analyst",
            persona=(
                "You are the Data Analyst. Right now you have no data tools "
                "yet (except optionally make_sketch) — this step is "
                "discussion only. You're a peer of the Product Manager, not "
                "their subordinate. Help figure out what real data would "
                "actually answer the business objective — which real Swiss "
                "sources, which fields." + BE_CONCISE + STATUS_TAG_INSTRUCTION
            ),
            model=MODEL,
            tools=[MAKE_SKETCH_SCHEMA],
            tool_impls={"make_sketch": call_make_sketch},
        )

        data_analyst_with_tools = Agent(
            client=client,
            name="Data Analyst",
            persona=(
                "You are the Data Analyst. You now have real tools — "
                "attempt_scrape, search_open_data, download_dataset, "
                "preview_data, discard_dataset, make_sketch — and decide "
                "yourself, turn by turn, whether and which to use. Try real "
                "platforms one at a time and look at each real result before "
                "trying another; if scraping is blocked, pivot to "
                "search_open_data. As soon as search_open_data returns a "
                "candidate resource that looks plausible, actually call "
                "download_dataset on its resource_id right away — don't just "
                "say you'll download it, and don't call search_open_data "
                "again on the same candidate instead of downloading it. We "
                "need real Swiss individual, single-"
                "apartment-level rental records (one row per listing) — "
                "aggregated statistics are NOT acceptable, they don't let us "
                "predict a price for one specific apartment, and neither is "
                "data about the wrong topic (e.g. taxes, exhibitions, plant "
                "species) just because it's Swiss and downloadable. After "
                "every download, you MUST call preview_data and look at the "
                "real column names it returns before saying anything about "
                "whether the data is individual-level rental data — never "
                "declare a dataset 'confirmed' or 'verified' without having "
                "actually called preview_data on it. If the real columns show "
                "it's aggregated (e.g. one row per municipality or per year, "
                "columns like averages/medians/totals), not about rental "
                "apartments at all, or otherwise unusable (e.g. unnamed/"
                "garbled columns), say so plainly, call discard_dataset to "
                "really delete that file, and keep searching with different "
                "terms, datasets, or platforms — do not settle for it and do "
                "not leave an unsuitable file lying around just to have "
                "something to work with. Report only what these tools "
                "actually return, never invent numbers or claim a check you "
                "didn't actually do." + BE_CONCISE + STATUS_TAG_INSTRUCTION
            ),
            model=MODEL,
            tools=[
                ATTEMPT_SCRAPE_SCHEMA,
                SEARCH_OPEN_DATA_SCHEMA,
                DOWNLOAD_DATASET_SCHEMA,
                PREVIEW_DATA_SCHEMA,
                DISCARD_DATASET_SCHEMA,
                MAKE_SKETCH_SCHEMA,
            ],
            tool_impls={
                "attempt_scrape": call_attempt_scrape,
                "search_open_data": call_search_open_data,
                "download_dataset": call_download_dataset,
                "preview_data": call_preview_data,
                "discard_dataset": call_discard_dataset,
                "make_sketch": call_make_sketch,
            },
        )

        data_engineer_collecting = Agent(
            client=client,
            name="Data Engineer",
            persona=(
                "You are the Data Engineer. This step belongs to the Data "
                "Analyst — they own the actual scraping/search/download "
                "tools and decide what to try next; you don't have data "
                "tools yet here either (except optionally make_sketch). "
                "Weigh in on ingestion/pipeline concerns as they go: file "
                "format and encoding, whether a source's structure looks "
                "stable enough to scrape again later, rate-limiting or "
                "blocking behavior worth designing around, how the raw file "
                "would actually land in a pipeline once it's real. Don't "
                "drive the search yourself, and don't duplicate the Data "
                "Analyst's call on whether a dataset is individual-level — "
                "that judgment is theirs to make." + BE_CONCISE + STATUS_TAG_INSTRUCTION
            ),
            model=MODEL,
            tools=[MAKE_SKETCH_SCHEMA],
            tool_impls={"make_sketch": call_make_sketch},
        )

        data_engineer_notools = Agent(
            client=client,
            name="Data Engineer",
            persona=(
                "You are the Data Engineer. Right now you have no data tools "
                "yet (except optionally make_sketch) — this step is "
                "discussion only. You're a peer of the Product Manager, not "
                "their subordinate. Discuss how you'll prepare and store the "
                "downloaded data: cleaning approach, what a good database "
                "table/schema would look like. No analysis or interpretation "
                "— just structure and storage planning." + BE_CONCISE + STATUS_TAG_INSTRUCTION
            ),
            model=MODEL,
            tools=[MAKE_SKETCH_SCHEMA],
            tool_impls={"make_sketch": call_make_sketch},
        )

        data_engineer_with_tools = Agent(
            client=client,
            name="Data Engineer",
            persona=(
                "You are the Data Engineer. You now have real tools — "
                "preview_data, profile_data, clean_data, store_to_database, "
                "run_sql_query, make_sketch — and decide yourself, turn by "
                "turn, whether and which to use. Preview and profile the real "
                "downloaded data first — discuss what you actually find: "
                "real column data types, duplicate/missing-value counts, and "
                "what table schema (the data model) makes sense for storing "
                "it, mentioning real ingestion/loading steps where relevant. "
                "Clean it based on that, store it in a real SQLite database, "
                "then run a real SQL query to verify the storage worked "
                "(e.g. a COUNT, or the course's AVG(price) GROUP BY rooms "
                "example). This is data preparation and engineering, not "
                "analysis — don't interpret trends or draw conclusions. "
                "Report only what these tools actually return."
                + BE_CONCISE
                + STATUS_TAG_INSTRUCTION
            ),
            model=MODEL,
            tools=[
                PREVIEW_DATA_SCHEMA,
                PROFILE_DATA_SCHEMA,
                CLEAN_DATA_SCHEMA,
                STORE_TO_DATABASE_SCHEMA,
                RUN_SQL_QUERY_SCHEMA,
                MAKE_SKETCH_SCHEMA,
            ],
            tool_impls={
                "preview_data": call_preview_data,
                "profile_data": call_profile_data,
                "clean_data": call_clean_data,
                "store_to_database": call_store_to_database,
                "run_sql_query": call_run_sql_query,
                "make_sketch": call_make_sketch,
            },
        )

        # --- Orchestration -----------------------------------------------

        turn_count = 0
        started_at = time.monotonic()
        transcript: list[dict] = []
        # Which step is currently active, so a stopped/timed-out outcome can
        # say where it happened rather than just that it happened.
        current_step = {"step": 1, "step_label": "Business objective"}

        def time_left() -> bool:
            return (time.monotonic() - started_at) < MAX_RUNTIME_SECONDS

        def stream_turn(speaker: str, text: str, used_tool: bool, step: int, step_label: str):
            nonlocal turn_count
            turn_count += 1
            q.put(
                _sse(
                    "progress",
                    {"stage": f"Turn {turn_count}", "step": step, "step_label": step_label},
                )
            )
            q.put(_sse("turn", {"speaker": speaker, "text": text, "action": used_tool}))
            history.append({"kind": "turn", "speaker": speaker, "text": text, "action": used_tool})
            time.sleep(TURN_DELAY_SECONDS)

        def run_phase(
            agents: list[Agent],
            step: int,
            step_label: str,
            sub_label: str,
            goal: str,
            has_tools: bool,
            max_turns_override: int | None = None,
            min_turns_override: int | None = None,
        ):
            current_step["step"] = step
            current_step["step_label"] = step_label
            q.put(
                _sse(
                    "phase_start",
                    {"step": step, "step_label": step_label, "sub_label": sub_label, "goal": goal},
                )
            )
            history.append(
                {
                    "kind": "phase",
                    "step": step,
                    "step_label": step_label,
                    "sub_label": sub_label,
                    "goal": goal,
                }
            )
            transcript.append(
                {
                    "speaker": "system",
                    "text": (
                        f"Step {step}/4 — {step_label} ({sub_label}). Goal: {goal} "
                        "Stay strictly on THIS goal — don't jump ahead to later "
                        "steps (e.g. don't discuss cleaning approach or database "
                        "design before data is even collected). Data ENGINEERING "
                        "topics are fair game whenever relevant — data types, "
                        "the database/table schema (the 'data model'), ingestion "
                        "steps, keys, formats. What's OUT OF SCOPE for this whole "
                        "demo is DATA ANALYSIS/MODELING — never name or discuss "
                        "an analysis or predictive-modeling technique (outlier "
                        "detection, scoring, statistics, regression, decision "
                        "trees, neural networks, or any other algorithm) — that's "
                        "a separate, not-yet-built part of the process; at most, "
                        "note in passing that analysis comes later, with zero "
                        "detail. If your peer's message drifts into something "
                        "premature, don't follow along — redirect them back to "
                        "this step's goal instead."
                    ),
                }
            )

            min_turns = min_turns_override or (
                MIN_TURNS_ACTION if has_tools else MIN_TURNS_DISCUSSION
            )
            max_turns = max_turns_override or (
                MAX_TURNS_ACTION if has_tools else MAX_TURNS_DISCUSSION
            )

            next_ready = {a.name: False for a in agents}
            turns_in_phase = 0
            turn_idx = 0

            while (
                turns_in_phase < max_turns
                and turn_count < MAX_TURNS
                and time_left()
                and not stop_event.is_set()
            ):
                speaker = agents[turn_idx % len(agents)]
                raw_reply, used_tool = speaker.speak(transcript)
                match = STATUS_TAG_RE.search(raw_reply or "")
                status = match.group(1).upper() if match else "CONTINUE"
                clean_reply = STATUS_TAG_RE.sub("", raw_reply or "").strip()
                clean_reply = SPEAKER_PREFIX_RE.sub("", clean_reply)

                next_ready[speaker.name] = status == "NEXT"
                transcript.append({"speaker": speaker.name, "text": clean_reply})
                stream_turn(speaker.name, clean_reply, used_tool, step, step_label)
                turns_in_phase += 1
                turn_idx += 1

                if turns_in_phase >= min_turns and all(next_ready.values()):
                    break

        def should_stop() -> bool:
            return turn_count >= MAX_TURNS or not time_left() or stop_event.is_set()

        # Step 1: Business objective — fixed, not agent-decided.
        business_objective = _build_business_objective()
        q.put(
            _sse(
                "phase_start",
                {
                    "step": 1,
                    "step_label": "Business objective",
                    "sub_label": "",
                    "goal": business_objective,
                },
            )
        )
        history.append(
            {
                "kind": "phase",
                "step": 1,
                "step_label": "Business objective",
                "sub_label": "",
                "goal": business_objective,
            }
        )
        transcript.append({"speaker": product_manager.name, "text": business_objective})
        stream_turn(product_manager.name, business_objective, False, 1, "Business objective")

        ack_reply, ack_used_tool = data_analyst_notools.speak(transcript)
        ack_clean = STATUS_TAG_RE.sub("", ack_reply or "").strip()
        ack_clean = SPEAKER_PREFIX_RE.sub("", ack_clean)
        transcript.append({"speaker": data_analyst_notools.name, "text": ack_clean})
        stream_turn(data_analyst_notools.name, ack_clean, ack_used_tool, 1, "Business objective")

        # Step 1b: Before locking in the data requirements, have all three
        # peers briefly consider what OTHER objectives this same rental
        # dataset could serve — each from their own role's angle — then
        # agree to stick with the price-prediction objective. Round-robin
        # across all three (not just PM + one specialist) since this is
        # about the product as a whole, not one step's deliverable.
        if not should_stop():
            run_phase(
                [product_manager, data_analyst_notools, data_engineer_notools],
                1,
                "Business objective",
                "other objectives to consider",
                "Before nailing down data requirements, briefly brainstorm what OTHER "
                "objectives this same rental dataset could serve — one or two ideas each, "
                "from your own angle. Product Manager: business/product value, e.g. a "
                "market-transparency tool for tenants, an investment-screening tool for "
                "landlords, or flagging overpriced/underpriced listings. Data Analyst: "
                "analytical questions the data could answer, e.g. which features drive "
                "price most, or how prices differ across Zurich districts or over time. "
                "Data Engineer: what other data products the same pipeline could feed, "
                "e.g. a live pricing API, a refreshed dashboard, or scheduled re-scraping. "
                "Keep it short, then explicitly agree you're sticking with the "
                "price-prediction objective for this project.",
                has_tools=False,
                min_turns_override=3,
            )

        # Step 2: Defining appropriate data — discussion only.
        # Stop/turn-cap/time checks happen *between* steps, not just inside
        # run_phase's own loop — otherwise hitting Stop mid-step would still
        # race through every remaining step emitting empty dividers before
        # reaching "done", instead of ending right where the user stopped it.
        if not should_stop():
            run_phase(
                [product_manager, data_analyst_notools],
                2,
                "Defining appropriate data",
                "discussion",
                "Decide together what real data would let us build this price-prediction "
                "model — which sources, which fields. We need individual, "
                "single-apartment-level records (one row per listing) — not pre-aggregated "
                "statistics (e.g. medians/percentiles by room count or district) — since a "
                "price-prediction model needs per-apartment examples to learn from.",
                has_tools=False,
            )

        # Step 3: Collecting data — real tools.
        if not should_stop():
            run_phase(
                [product_manager, data_analyst_with_tools, data_engineer_collecting],
                3,
                "Collecting data",
                "action",
                "Actually try to obtain real Swiss rental data now, using your real tools. "
                "After every download, call preview_data and check the real column names "
                "before claiming anything about whether the dataset is at the "
                "individual-apartment level (one row per listing) or just aggregated "
                "statistics — mention this explicitly, grounded in what preview_data "
                "actually showed. Keep searching — with different search terms, different "
                "datasets, different platforms — until you genuinely find and confirm data "
                "at the individual-apartment level. Aggregated or wrong-topic statistics "
                "are NOT an acceptable substitute, no matter how many attempts it takes: if "
                "preview_data shows a result is aggregated, not actually about rental "
                "apartments, or otherwise unusable (e.g. unnamed/garbled columns), call "
                "discard_dataset to really delete that file, say so, and try a different "
                "angle rather than keeping it around. Step 4 needs an actual, verified, "
                "listing-level file to work with. Data Engineer: react to what's actually "
                "being found — flag real ingestion/pipeline concerns (format, encoding, how "
                "stable the source looks for scraping again later, rate-limiting/blocking "
                "behavior) — but let the Data Analyst drive the search and make the "
                "individual-level-vs-aggregated call.",
                has_tools=True,
                max_turns_override=MAX_TURNS_COLLECT,
            )

            # No individual-level dataset ever got confirmed within the
            # search budget — really download a known, always-available
            # real dataset (aggregated, not individual-level) as a
            # guaranteed last resort, rather than ending Step 3 with
            # nothing. Still a real HTTP download, same download_dataset()
            # used for every other Step 3 download.
            if not dataset_ready["value"]:
                fallback_download = download_dataset(
                    resource_url=FALLBACK_DATASET_URL,
                    resource_format=FALLBACK_DATASET_FORMAT,
                    out_path=str(FALLBACK_PATH),
                    on_progress=on_progress,
                )
                if fallback_download.get("success"):
                    current_file["path"] = str(FALLBACK_PATH)
                    current_file["format"] = FALLBACK_DATASET_FORMAT
                    dataset_ready["value"] = True
                    fallback_reason = (
                        "it's aggregated (one row per district/room-count/year), not "
                        "individual-apartment listings"
                    )
                    download_result.clear()
                    download_result.update(
                        {
                            "success": True,
                            "fallback": True,
                            "format": FALLBACK_DATASET_FORMAT,
                            "bytes": fallback_download.get("bytes"),
                            "resource_url": FALLBACK_DATASET_URL,
                            "dataset_title": FALLBACK_DATASET_TITLE,
                            "dataset_organization": FALLBACK_DATASET_ORGANIZATION,
                            "dataset_url": FALLBACK_DATASET_PAGE_URL,
                            "reason_rejected": fallback_reason,
                        }
                    )
                    capture_preview(
                        download_preview_result, current_file["path"], current_file["format"]
                    )

                    # The decision has to be voiced by an agent, not a
                    # silent system switch — prime the Product Manager with
                    # the real facts and have it actually say so, the same
                    # speak-then-stream pattern used for Step 1's
                    # acknowledgment turn. The Product Manager (not the
                    # Data Analyst) says this specifically so we don't hand
                    # a tool-bearing agent a reason to call discard_dataset
                    # again on the file we just downloaded.
                    transcript.append(
                        {
                            "speaker": "system",
                            "text": (
                                "No individual-apartment-level dataset could be confirmed "
                                "within the search budget. Falling back to a real, "
                                f'always-available dataset: "{FALLBACK_DATASET_TITLE}" '
                                f"({FALLBACK_DATASET_ORGANIZATION}) — real Zurich rent-survey "
                                f"data, but {fallback_reason}. Product Manager: state, in ONE "
                                "or TWO short natural sentences and without calling any tools, "
                                "that the team is going with this real dataset as the best "
                                "available option rather than having nothing to work with — "
                                "name it, and be upfront that it's an aggregated substitute, "
                                "not genuine individual-level data."
                            ),
                        }
                    )
                    fallback_reply, fallback_used_tool = product_manager.speak(transcript)
                    fallback_clean = STATUS_TAG_RE.sub("", fallback_reply or "").strip()
                    fallback_clean = SPEAKER_PREFIX_RE.sub("", fallback_clean)
                    transcript.append({"speaker": product_manager.name, "text": fallback_clean})
                    stream_turn(
                        product_manager.name,
                        fallback_clean,
                        fallback_used_tool,
                        3,
                        "Collecting data",
                    )
                # If even the guaranteed fallback download fails (e.g. a
                # real network issue), fall through — the "incomplete"
                # outcome below still applies honestly.

            step3_result = {
                "step": 3,
                "step_label": "Collecting data",
                "opendata": dict(opendata_result),
                "download": dict(download_result),
                "preview": dict(download_preview_result),
            }
            q.put(_sse("phase_done", step3_result))
            history.append({"kind": "phase_result", "step": 3, "data": step3_result})

        # Step 4: Preparing & storing data — discuss, then real tools.
        # Only proceed if step 3 actually ended with a real, undiscarded,
        # verified-individual-level file — never clean/store a file that was
        # never confirmed, or run step 4 against nothing at all.
        if not should_stop() and dataset_ready["value"]:
            run_phase(
                [product_manager, data_engineer_notools],
                4,
                "Preparing & storing data",
                "planning",
                "Briefly discuss how you'll clean and store the downloaded data before doing "
                "it. Ground this in the REAL tool you actually have: store_to_database "
                "writes to a real local SQLite file via Python's stdlib sqlite3 — not "
                "PostgreSQL, MySQL, or any other system. Keep this planning short and "
                "concrete, tied to that real tool, not a hypothetical enterprise setup.",
                has_tools=False,
            )
            if not should_stop():
                run_phase(
                    [product_manager, data_engineer_with_tools],
                    4,
                    "Preparing & storing data",
                    "execution",
                    "Really clean the downloaded data, store it in the real SQLite database, "
                    "and verify it with a real SQL query. Once that's verified, wrap up: you "
                    "may note in ONE short clause that Exploratory Data Analysis (EDA) is "
                    "the next step in the process — nothing more. Do NOT describe how you'd "
                    "do EDA, do NOT name or discuss any modeling technique, algorithm, or "
                    "statistical method (regression, decision trees, neural networks, etc.) "
                    "— that's a separate, not-yet-built part of the process.",
                    has_tools=True,
                )
            step4_result = {
                "step": 4,
                "step_label": "Preparing & storing data",
                "profile": dict(profile_result),
                "clean": dict(clean_result),
                "store": dict(store_result),
                "sql": dict(sql_result),
                "sketch": dict(sketch_result),
                "preview": dict(clean_preview_result),
            }
            q.put(_sse("phase_done", step4_result))
            history.append({"kind": "phase_result", "step": 4, "data": step4_result})
            if download_result.get("fallback"):
                outcome = {
                    "status": "completed_with_fallback",
                    "message": (
                        "Completed all 4 steps using a fallback (aggregated/best-available) "
                        "dataset — no individual-apartment-level data was ever confirmed."
                    ),
                }
            else:
                outcome = {"status": "completed", "message": "Completed all 4 steps."}
            q.put(_sse("done", outcome))
        elif not should_stop():
            # Step 3 ended without ever landing on genuine individual-level
            # data (everything found was aggregated/wrong-topic and got
            # discarded, or the search budget ran out first) — say so
            # honestly instead of faking step 4 against no real file.
            outcome = {
                "status": "incomplete",
                "message": (
                    "No individual-apartment-level dataset could be confirmed within "
                    "the search budget — every candidate turned out to be aggregated "
                    "or off-topic and was discarded. Preparing & storing data was "
                    "skipped since there's no real file to work with."
                ),
            }
            q.put(_sse("done", {"incomplete": True, **outcome}))
        else:
            # should_stop() was already true before step 3/4 even ran — either
            # the user clicked Stop, or the turn/time safety net kicked in.
            where = f"during Step {current_step['step']}/4 · {current_step['step_label']}"
            if stop_event.is_set():
                outcome = {"status": "stopped", "message": f"Stopped by the user {where}."}
            else:
                minutes = MAX_RUNTIME_SECONDS // 60
                outcome = {
                    "status": "timed_out",
                    "message": (
                        f"Hit the safety net ({MAX_TURNS} turns or {minutes} minutes) {where}."
                    ),
                }
            q.put(_sse("done", outcome))
    except Exception as exc:  # surface backend errors to the browser instead of hanging
        outcome = {"status": "error", "message": str(exc)}
        # Named "app_error", not "error" — EventSource treats a literal
        # "error" SSE event as indistinguishable from its own native
        # connection-failure event, so the frontend's genuine
        # connection-lost handler was silently stomping this message.
        q.put(_sse("app_error", {"message": str(exc)}))
    finally:
        # Saved regardless of how the run ended (finished, stopped, or
        # errored) so a partial conversation is never silently lost.
        _save_conversation_history(history, run_started_at, outcome)
        q.put(None)  # sentinel: stop the stream


@app.get("/api/stream")
def stream():
    """Start a fresh demo run and stream it to the browser as SSE events."""
    stop_event.clear()
    q: "queue.Queue" = queue.Queue()
    threading.Thread(target=_run_demo, args=(q,), daemon=True).start()

    def event_generator():
        # A single turn can legitimately take a while (a slow model reply, a
        # multi-MB download). Without something sent regularly, some proxies
        # / port-forwarding layers treat the connection as idle and kill it,
        # which the browser reports as "connection lost" even though the
        # backend is still working. An SSE comment line (browsers ignore
        # lines starting with ':') sent whenever nothing real has happened
        # in heartbeat_seconds keeps the connection visibly alive.
        heartbeat_seconds = 15
        while True:
            try:
                item = q.get(timeout=heartbeat_seconds)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            if item is None:
                break
            yield item

    return StreamingResponse(event_generator(), media_type="text/event-stream")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)

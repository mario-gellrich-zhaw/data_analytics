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
data structurally, they don't derive insights. Each step is a bounded 2-party
conversation (Product Manager plus whichever specialist owns that step) that
ends once both sides say, via a status tag, that it's genuinely done; the
whole run ends after step 4 — there's no open-ended looping afterward.

A manual Stop (POST /api/stop) ends the run cleanly at any point, and a
generous safety net (max turns / max wall-clock time) bounds it regardless.
"""

import json
import queue
import re
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, StreamingResponse
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
TURN_DELAY_SECONDS = 4       # pace the conversation so a class can read along
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
# it gets extra room beyond the normal action budget above.
MAX_TURNS_COLLECT = 26
MAX_TURNS = 80               # safety net: a live demo shouldn't run forever if nobody stops it
MAX_RUNTIME_SECONDS = 20 * 60  # ...or 20 minutes, whichever comes first
STATUS_TAG_RE = re.compile(r"\s*\[STATUS:\s*(CONTINUE|NEXT)\]\s*$", re.IGNORECASE)
# The model occasionally mimics the "Speaker: text" formatting it sees for
# the *other* agent's turns (see agents.py's _messages_for) and mistakenly
# prefixes its OWN reply with a name — including sometimes the wrong one.
# Once that lands in the shared transcript, every future turn sees it as
# "how this agent talks" and the pattern self-reinforces. Stripped here,
# mechanically, before anything is stored — no persona wording alone proved
# reliable enough to prevent it.
SPEAKER_PREFIX_RE = re.compile(r"^(Product Manager|Data Analyst|Data Engineer)\s*:\s*", re.IGNORECASE)

STATUS_TAG_INSTRUCTION = (
    " End every message on a new line with exactly '[STATUS: CONTINUE]' if "
    "there's more to do for the CURRENT step, or '[STATUS: NEXT]' once you "
    "think it's genuinely done. Never prefix your message with a name or "
    "role label (e.g. don't start with 'Data Engineer:' or 'Data Analyst:') "
    "— just write your reply directly, the UI already shows who's speaking."
)

BE_CONCISE = (
    " Talk like a real colleague in a quick chat, not a report — short, "
    "natural sentences (1-2 sentences), no restating the question, no "
    "filler. Don't default to bullet lists: most messages should just be "
    "plain conversational text. Only switch to a short bullet list or tiny "
    "table when you're genuinely comparing several distinct items AND prose "
    "would be more awkward than a list — and even then keep it to a "
    "handful of items with short clarifying notes, not bare labels and not "
    "full sentences. If there are many possible items, don't enumerate them "
    "all: mention a few naturally instead, e.g. 'we could use data such as "
    "the FSO price index or ImmoScout24 listings' rather than listing "
    "every option."
)

BUSINESS_OBJECTIVE = (
    "Welcome, everyone! Our goal for this project is to build a "
    "price-prediction model for rental apartments in the canton of Zurich. "
    "To get there, we'll follow our data analytics process model, starting "
    "with figuring out what data we actually need."
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOAD_PATH = BASE_DIR / "downloaded_dataset.csv"
CLEANED_PATH = BASE_DIR / "cleaned_dataset.csv"
DB_PATH = BASE_DIR / "rental_data.db"

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# One demo at a time (this is a single-instructor classroom tool, not a
# multi-user service) — a plain module-level flag is enough to let the
# frontend's Stop button end an in-progress run.
stop_event = threading.Event()


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/api/stop")
def stop():
    stop_event.set()
    return {"stopping": True}


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _run_demo(q: "queue.Queue"):
    try:
        def on_progress(stage: str):
            q.put(_sse("progress", {"stage": stage}))

        # Each run starts genuinely fresh — no file from a previous run can
        # leak in and be mistaken for real data in this one.
        for stale_path in (DOWNLOAD_PATH, CLEANED_PATH, DB_PATH):
            stale_path.unlink(missing_ok=True)

        # --- shared state written by tool calls, read for phase-end cards ---
        opendata_result = {}
        download_result = {}
        download_preview_result = {}  # first 10 raw rows, captured automatically right after download
        profile_result = {}
        clean_result = {}
        clean_preview_result = {}  # first 10 cleaned rows, captured automatically right after cleaning
        store_result = {}
        sql_result = {}
        sketch_result = {}
        resource_lookup = {}  # short id -> real resource info, from search_open_data
        current_file = {"path": str(DOWNLOAD_PATH), "format": "CSV"}  # what preview/profile/clean act on right now
        dataset_ready = {"value": False}  # True only while current_file points at a real, undiscarded download

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

        MIN_LISTING_ROWS = 15  # a whole-canton listings dataset should clear this easily

        def check_looks_like_listing_data(path: str, data_format: str) -> tuple[bool, str]:
            # A real, code-level structural sanity check that runs on every
            # download regardless of whether the agent itself remembers to
            # call preview_data — prompting alone proved unreliable here
            # (the model sometimes skipped its own verification step and
            # declared success on an obviously aggregated file). This can't
            # judge topic/semantics, but it reliably catches the two most
            # common real failure shapes seen live: multi-header statistics
            # exports (mostly "Unnamed: N" columns after pandas parses them)
            # and pivot/summary tables (a handful of rows).
            try:
                result = preview_data(path=path, data_format=data_format, n=50)
            except Exception as exc:
                return False, f"the file couldn't even be read as a table ({exc})"
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
            if n_rows < MIN_LISTING_ROWS:
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
            return {"query": result["query"], "total_found": result["total_found"], "datasets": agent_view_datasets}

        def call_download_dataset(resource_id: str):
            picked = resource_lookup.get(resource_id)
            if not picked:
                return {
                    "success": False,
                    "error": f"Unknown resource_id '{resource_id}'. Use an id exactly as returned by search_open_data.",
                }

            result = download_dataset(
                resource_url=picked["url"],
                resource_format=picked["format"],
                out_path=str(DOWNLOAD_PATH),
                on_progress=on_progress,
            )

            if result.get("success"):
                looks_ok, reason = check_looks_like_listing_data(str(DOWNLOAD_PATH), picked["format"])
                if not looks_ok:
                    # Automatically recognized as unsuitable (aggregated /
                    # not listing-level) — really delete it and report the
                    # rejection as the actual tool result, regardless of
                    # what the agent itself would have concluded.
                    Path(DOWNLOAD_PATH).unlink(missing_ok=True)
                    result["success"] = False
                    result["error"] = f"Downloaded, but automatically rejected: {reason}. Try a different dataset."
                    current_file["path"] = ""
                    current_file["format"] = ""
                    dataset_ready["value"] = False
                    download_preview_result.clear()
                else:
                    current_file["path"] = str(DOWNLOAD_PATH)
                    current_file["format"] = picked["format"]
                    dataset_ready["value"] = True
                    capture_preview(download_preview_result, current_file["path"], current_file["format"])
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
            result = discard_dataset(path=current_file["path"], reason=reason, on_progress=on_progress)
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
                return {"success": False, "error": "No dataset file is available yet — download (and confirm) one first."}
            return None

        def call_preview_data(n: int = 10):
            err = _no_file_error()
            if err:
                return err
            result = preview_data(path=current_file["path"], data_format=current_file["format"], n=n, on_progress=on_progress)
            return {"columns": result["columns"], "n_rows_shown": len(result["rows"])}

        def call_profile_data():
            err = _no_file_error()
            if err:
                return err
            result = profile_data(path=current_file["path"], data_format=current_file["format"], on_progress=on_progress)
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
                source_path=current_file["path"], db_path=str(DB_PATH), table_name=table_name, on_progress=on_progress
            )
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
            return {"ok": True}  # keep the tool-result text small; the UI reads sketch_result directly

        # --- Agents ----------------------------------------------------------

        product_manager = Agent(
            client=client,
            name="Product Manager",
            persona=(
                "You are the Product Manager on a non-commercial ZHAW student "
                "project. You have no data tools yourself (you may optionally "
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
                "sources, which fields."
                + BE_CONCISE
                + STATUS_TAG_INSTRUCTION
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
                "didn't actually do."
                + BE_CONCISE
                + STATUS_TAG_INSTRUCTION
            ),
            model=MODEL,
            tools=[ATTEMPT_SCRAPE_SCHEMA, SEARCH_OPEN_DATA_SCHEMA, DOWNLOAD_DATASET_SCHEMA, PREVIEW_DATA_SCHEMA, DISCARD_DATASET_SCHEMA, MAKE_SKETCH_SCHEMA],
            tool_impls={
                "attempt_scrape": call_attempt_scrape,
                "search_open_data": call_search_open_data,
                "download_dataset": call_download_dataset,
                "preview_data": call_preview_data,
                "discard_dataset": call_discard_dataset,
                "make_sketch": call_make_sketch,
            },
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
                "— just structure and storage planning."
                + BE_CONCISE
                + STATUS_TAG_INSTRUCTION
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

        def time_left() -> bool:
            return (time.monotonic() - started_at) < MAX_RUNTIME_SECONDS

        def stream_turn(speaker: str, text: str, used_tool: bool, step: int, step_label: str):
            nonlocal turn_count
            turn_count += 1
            q.put(_sse("progress", {"stage": f"Turn {turn_count}", "step": step, "step_label": step_label}))
            q.put(_sse("turn", {"speaker": speaker, "text": text, "action": used_tool}))
            time.sleep(TURN_DELAY_SECONDS)

        def run_phase(agent_a: Agent, agent_b: Agent, step: int, step_label: str, sub_label: str, goal: str, has_tools: bool, max_turns_override: int | None = None):
            q.put(_sse("phase_start", {"step": step, "step_label": step_label, "sub_label": sub_label, "goal": goal}))
            transcript.append({
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
            })

            min_turns = MIN_TURNS_ACTION if has_tools else MIN_TURNS_DISCUSSION
            max_turns = max_turns_override or (MAX_TURNS_ACTION if has_tools else MAX_TURNS_DISCUSSION)

            next_ready = {agent_a.name: False, agent_b.name: False}
            turns_in_phase = 0
            speaker, listener = agent_a, agent_b

            while (
                turns_in_phase < max_turns
                and turn_count < MAX_TURNS
                and time_left()
                and not stop_event.is_set()
            ):
                raw_reply, used_tool = speaker.speak(transcript)
                match = STATUS_TAG_RE.search(raw_reply or "")
                status = match.group(1).upper() if match else "CONTINUE"
                clean_reply = STATUS_TAG_RE.sub("", raw_reply or "").strip()
                clean_reply = SPEAKER_PREFIX_RE.sub("", clean_reply)

                next_ready[speaker.name] = status == "NEXT"
                transcript.append({"speaker": speaker.name, "text": clean_reply})
                stream_turn(speaker.name, clean_reply, used_tool, step, step_label)
                turns_in_phase += 1

                if turns_in_phase >= min_turns and all(next_ready.values()):
                    break
                speaker, listener = listener, speaker

        def should_stop() -> bool:
            return turn_count >= MAX_TURNS or not time_left() or stop_event.is_set()

        # Step 1: Business objective — fixed, not agent-decided.
        q.put(_sse("phase_start", {"step": 1, "step_label": "Business objective", "sub_label": "", "goal": BUSINESS_OBJECTIVE}))
        transcript.append({"speaker": product_manager.name, "text": BUSINESS_OBJECTIVE})
        stream_turn(product_manager.name, BUSINESS_OBJECTIVE, False, 1, "Business objective")

        ack_reply, ack_used_tool = data_analyst_notools.speak(transcript)
        ack_clean = STATUS_TAG_RE.sub("", ack_reply or "").strip()
        ack_clean = SPEAKER_PREFIX_RE.sub("", ack_clean)
        transcript.append({"speaker": data_analyst_notools.name, "text": ack_clean})
        stream_turn(data_analyst_notools.name, ack_clean, ack_used_tool, 1, "Business objective")

        # Step 2: Defining appropriate data — discussion only.
        # Stop/turn-cap/time checks happen *between* steps, not just inside
        # run_phase's own loop — otherwise hitting Stop mid-step would still
        # race through every remaining step emitting empty dividers before
        # reaching "done", instead of ending right where the user stopped it.
        if not should_stop():
            run_phase(
                product_manager, data_analyst_notools, 2, "Defining appropriate data", "discussion",
                "Decide together what real data would let us build this price-prediction model — which "
                "sources, which fields. We need individual, single-apartment-level records (one row per "
                "listing) — not pre-aggregated statistics (e.g. medians/percentiles by room count or "
                "district) — since a price-prediction model needs per-apartment examples to learn from.",
                has_tools=False,
            )

        # Step 3: Collecting data — real tools.
        if not should_stop():
            run_phase(
                product_manager, data_analyst_with_tools, 3, "Collecting data", "action",
                "Actually try to obtain real Swiss rental data now, using your real tools. After every "
                "download, call preview_data and check the real column names before claiming anything "
                "about whether the dataset is at the individual-apartment level (one row per listing) or "
                "just aggregated statistics — mention this explicitly, grounded in what preview_data "
                "actually showed. Keep searching — with different search terms, different datasets, "
                "different platforms — until you genuinely find and confirm data at the individual-"
                "apartment level. Aggregated or wrong-topic statistics are NOT an acceptable substitute, no "
                "matter how many attempts it takes: if preview_data shows a result is aggregated, not "
                "actually about rental apartments, or otherwise unusable (e.g. unnamed/garbled columns), "
                "call discard_dataset to really delete that file, say so, and try a different angle rather "
                "than keeping it around. Step 4 needs an actual, verified, listing-level file to work with.",
                has_tools=True,
                max_turns_override=MAX_TURNS_COLLECT,
            )
            q.put(_sse("phase_done", {
                "step": 3, "step_label": "Collecting data",
                "opendata": dict(opendata_result), "download": dict(download_result),
                "preview": dict(download_preview_result),
            }))

        # Step 4: Preparing & storing data — discuss, then real tools.
        # Only proceed if step 3 actually ended with a real, undiscarded,
        # verified-individual-level file — never clean/store a file that was
        # never confirmed, or run step 4 against nothing at all.
        if not should_stop() and dataset_ready["value"]:
            run_phase(
                product_manager, data_engineer_notools, 4, "Preparing & storing data", "planning",
                "Briefly discuss how you'll clean and store the downloaded data before doing it. Ground "
                "this in the REAL tool you actually have: store_to_database writes to a real local "
                "SQLite file via Python's stdlib sqlite3 — not PostgreSQL, MySQL, or any other system. "
                "Keep this planning short and concrete, tied to that real tool, not a hypothetical "
                "enterprise setup.",
                has_tools=False,
            )
            if not should_stop():
                run_phase(
                    product_manager, data_engineer_with_tools, 4, "Preparing & storing data", "execution",
                    "Really clean the downloaded data, store it in the real SQLite database, and verify "
                    "it with a real SQL query. Once that's verified, wrap up: you may note in ONE short "
                    "clause that Exploratory Data Analysis (EDA) is the next step in the process — "
                    "nothing more. Do NOT describe how you'd do EDA, do NOT name or discuss any modeling "
                    "technique, algorithm, or statistical method (regression, decision trees, neural "
                    "networks, etc.) — that's a separate, not-yet-built part of the process.",
                    has_tools=True,
                )
            q.put(_sse("phase_done", {
                "step": 4, "step_label": "Preparing & storing data",
                "profile": dict(profile_result), "clean": dict(clean_result),
                "store": dict(store_result), "sql": dict(sql_result), "sketch": dict(sketch_result),
                "preview": dict(clean_preview_result),
            }))
            q.put(_sse("done", {}))
        elif not should_stop():
            # Step 3 ended without ever landing on genuine individual-level
            # data (everything found was aggregated/wrong-topic and got
            # discarded, or the search budget ran out first) — say so
            # honestly instead of faking step 4 against no real file.
            q.put(_sse("done", {
                "incomplete": True,
                "message": (
                    "No individual-apartment-level dataset could be confirmed within the search "
                    "budget — every candidate turned out to be aggregated or off-topic and was "
                    "discarded. Preparing & storing data was skipped since there's no real file to "
                    "work with."
                ),
            }))
        else:
            q.put(_sse("done", {}))
    except Exception as exc:  # surface backend errors to the browser instead of hanging
        q.put(_sse("error", {"message": str(exc)}))
    finally:
        q.put(None)  # sentinel: stop the stream


@app.get("/api/stream")
def stream():
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
        # in HEARTBEAT_SECONDS keeps the connection visibly alive.
        HEARTBEAT_SECONDS = 15
        while True:
            try:
                item = q.get(timeout=HEARTBEAT_SECONDS)
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

"""Tiny web backend for the agentic demo (FastAPI, not Flask).

Serves a single static page and one Server-Sent-Events endpoint that runs
the agentic conversation live. There is no fixed sequence of steps here:
the two agents share one growing transcript (so neither "forgets" what the
other already found out) and the expert agent decides for itself, turn by
turn, whether and which real tool to use — whether to try scraping a
platform, pivot to opendata.swiss, download a dataset, check its real
columns for a price field, search again if it's missing, or preview the
actual rows.

The exploration is open-ended: once the agents consider a "chapter" (a
sub-goal, e.g. "find real rental price data for Zurich") done, they ask
each other what to look into next and keep going — a new city, a time
comparison, a different indicator — for as long as the instructor wants.
That's bounded two ways: a manual Stop (POST /api/stop) the class can hit
any time, and a generous safety net (max turns / max wall-clock time) in
case nobody does.
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
    ANALYZE_DATA_SCHEMA,
    ATTEMPT_SCRAPE_SCHEMA,
    DOWNLOAD_DATASET_SCHEMA,
    PREVIEW_DATA_SCHEMA,
    SEARCH_OPEN_DATA_SCHEMA,
    analyze_data,
    attempt_scrape,
    download_dataset,
    preview_data,
    search_open_data,
)

load_dotenv()  # finds the .env at the repo root
client = OpenAI()

MODEL = "gpt-4o-mini"
TURN_DELAY_SECONDS = 4        # pace the conversation so a class can read along
MIN_TURNS_PER_CHAPTER = 6     # at least a few real exchanges before a chapter may end
MAX_TURNS_PER_CHAPTER = 18    # force a move-on if a chapter stalls (e.g. going in circles on cosmetic issues)
MAX_TURNS = 120               # safety net: a live demo shouldn't run forever if nobody stops it
MAX_RUNTIME_SECONDS = 20 * 60  # ...or 20 minutes, whichever comes first
STATUS_TAG_RE = re.compile(r"\s*\[STATUS:\s*(CONTINUE|NEXT)\]\s*$", re.IGNORECASE)

STATUS_RULE = (
    " Work in 'chapters'. Chapter 1's goal: find a legal, working way to get "
    "real Swiss rental apartment data, actually download it, and check "
    "whether it actually has rental PRICES (not just counts) — if it "
    "doesn't, search again with better terms instead of settling. Once a "
    "chapter's goal is genuinely met (real data downloaded, confirmed "
    "useful, previewed), propose a new, meaningfully different angle to "
    "explore next — a different city/canton, a time comparison, individual "
    "listings vs. aggregates, a related indicator, comparing two datasets — "
    "using the same real tools. Check the conversation so far for what's "
    "already been covered and don't repeat it. If you notice you and the "
    "other agent are repeating the same point without resolving it (e.g. "
    "going back and forth on renaming/cleaning cosmetic issues), stop "
    "circling: make a pragmatic call right now — proceed with the data as "
    "it is, or abandon it and search for a different one — rather than "
    "raising the same concern again. End every message on a new line with "
    "exactly '[STATUS: CONTINUE]' while still working the current chapter, "
    "or '[STATUS: NEXT]' once it's done and you're ready to move to a new "
    "one."
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DOWNLOAD_PATH = BASE_DIR / "downloaded_dataset.csv"

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

        opendata_result = {}
        download_result = {}
        analysis_result = {}
        preview_result = {}
        current_format = {"value": "CSV"}  # remembers the last successfully downloaded resource's format
        resource_lookup = {}  # short id -> {"url", "format", "dataset_title", ...} — keeps growing across chapters

        def call_attempt_scrape(site: str):
            return attempt_scrape(site=site, on_progress=on_progress)

        def call_search_open_data(query: str):
            result = search_open_data(query=query, on_progress=on_progress)
            opendata_result.clear()
            opendata_result.update(result)

            # Give each real resource a short, stable id (e.g. "r3") instead
            # of making the model retype a long URL to pick one — it's easy
            # to get an exact URL slightly wrong across several turns, and a
            # wrong "download" URL either 404s or silently fetches the wrong
            # (HTML) page. Picking *which* resource is still entirely the
            # agent's decision; this only makes referencing that choice
            # reliable. Ids accumulate across the whole session (chapters
            # included) so an id from an earlier search stays valid.
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
            download_result.clear()
            download_result.update(result)
            download_result["resource_url"] = picked["url"]
            download_result["dataset_title"] = picked["dataset_title"]
            download_result["dataset_organization"] = picked["dataset_organization"]
            download_result["dataset_url"] = picked["dataset_url"]

            if result.get("success"):
                current_format["value"] = picked["format"]
            return result

        def call_analyze_data():
            result = analyze_data(
                path=str(DOWNLOAD_PATH), data_format=current_format["value"], on_progress=on_progress
            )
            analysis_result.clear()
            analysis_result.update(result)
            return result

        def call_preview_data(n: int = 10):
            result = preview_data(
                path=str(DOWNLOAD_PATH), data_format=current_format["value"], n=n, on_progress=on_progress
            )
            preview_result.clear()
            preview_result.update(result)
            return {"columns": result["columns"], "n_rows_shown": len(result["rows"])}

        researcher = Agent(
            client=client,
            name="Data Researcher",
            persona=(
                "You are a pragmatic data researcher preparing a non-commercial "
                "student project at ZHAW (a Swiss university of applied "
                "sciences). You need real Swiss rental apartment data — "
                "ideally with actual rental PRICES, not just counts — for "
                "teaching purposes only. You have no tools yourself: react to "
                "what the Data Source Expert actually finds, downloads, and "
                "analyzes, and push back if something seems off (e.g. a "
                "platform that was already tried and blocked, or a dataset "
                "with no price column). BE TERSE: one short sentence, max ~12 "
                "words, every single message — no pleasantries, no 'let me "
                "know what you find', no restating what was just said. Just "
                "the essential reaction or the next question."
                + STATUS_RULE
            ),
            model=MODEL,
        )

        expert = Agent(
            client=client,
            name="Data Source Expert",
            persona=(
                "You are an expert on the Swiss real estate data landscape. "
                "You have real tools — attempt_scrape, search_open_data, "
                "download_dataset, analyze_data, preview_data — and you "
                "decide yourself, turn by turn, whether and which one to use "
                "next. Try real platforms one at a time and look at each "
                "real result before trying another; if scraping is blocked, "
                "pivot to search_open_data; pick a real dataset/resource from "
                "what it returns and download it; use analyze_data to check "
                "its real columns for a price field — if there isn't one, "
                "search again with different terms instead of settling. "
                "Report only what these tools actually return, never invent "
                "numbers. Keep messages short and natural, more detailed only "
                "when discussing real data quality."
                + STATUS_RULE
            ),
            model=MODEL,
            tools=[
                ATTEMPT_SCRAPE_SCHEMA,
                SEARCH_OPEN_DATA_SCHEMA,
                DOWNLOAD_DATASET_SCHEMA,
                ANALYZE_DATA_SCHEMA,
                PREVIEW_DATA_SCHEMA,
            ],
            tool_impls={
                "attempt_scrape": call_attempt_scrape,
                "search_open_data": call_search_open_data,
                "download_dataset": call_download_dataset,
                "analyze_data": call_analyze_data,
                "preview_data": call_preview_data,
            },
        )

        turn_count = 0
        started_at = time.monotonic()

        def stream_turn(speaker: str, text: str, used_tool: bool):
            nonlocal turn_count
            turn_count += 1
            q.put(_sse("progress", {"stage": f"Turn {turn_count}"}))
            q.put(_sse("turn", {"speaker": speaker, "text": text, "action": used_tool}))
            time.sleep(TURN_DELAY_SECONDS)

        def chapter_snapshot() -> dict:
            return {
                "opendata": dict(opendata_result),
                "download": dict(download_result),
                "analysis": dict(analysis_result),
                "preview": dict(preview_result),
            }

        topic = (
            "We need real Swiss rental apartment data for a non-commercial "
            "ZHAW course project — ideally with actual rental prices. How "
            "should we start?"
        )
        transcript = [{"speaker": researcher.name, "text": topic}]
        stream_turn(researcher.name, topic, False)

        speaker, listener = expert, researcher
        next_ready = {researcher.name: False, expert.name: False}
        chapter = 1
        turns_in_chapter = 1  # the opening topic above counts as chapter 1's first turn

        def time_left() -> bool:
            return (time.monotonic() - started_at) < MAX_RUNTIME_SECONDS

        while turn_count < MAX_TURNS and time_left() and not stop_event.is_set():
            raw_reply, used_tool = speaker.speak(transcript)
            match = STATUS_TAG_RE.search(raw_reply or "")
            status = match.group(1).upper() if match else "CONTINUE"
            clean_reply = STATUS_TAG_RE.sub("", raw_reply or "").strip()

            next_ready[speaker.name] = status == "NEXT"
            transcript.append({"speaker": speaker.name, "text": clean_reply})
            stream_turn(speaker.name, clean_reply, used_tool)
            turns_in_chapter += 1

            stalled = turns_in_chapter >= MAX_TURNS_PER_CHAPTER
            if stalled:
                transcript.append(
                    {
                        "speaker": "system",
                        "text": (
                            "This chapter has gone on a while without wrapping up — move on to "
                            "a new angle now."
                        ),
                    }
                )

            if (turns_in_chapter >= MIN_TURNS_PER_CHAPTER and all(next_ready.values())) or stalled:
                q.put(_sse("chapter_done", {"chapter": chapter, **chapter_snapshot()}))
                chapter += 1
                turns_in_chapter = 0
                next_ready = {researcher.name: False, expert.name: False}
                download_result.clear()
                analysis_result.clear()
                preview_result.clear()

            speaker, listener = listener, speaker

        # Always emit whatever the current (possibly unfinished) chapter has
        # found so far, so the class sees the real state even if stopped or
        # capped mid-chapter — but skip an empty duplicate if a chapter just
        # closed right above.
        if turns_in_chapter > 0:
            q.put(_sse("chapter_done", {"chapter": chapter, **chapter_snapshot()}))

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

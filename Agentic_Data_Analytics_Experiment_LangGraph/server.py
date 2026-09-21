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
data structurally, they don't derive insights. Turn-taking and tool-calling
are driven by a LangGraph `StateGraph` (see graph.py): each phase streams
the compiled graph until its agents reach consensus (a status tag) or a
turn cap is hit.

A manual Stop (POST /api/stop) ends the run cleanly at any point, and a
generous safety net (max turns / max wall-clock time) bounds it regardless.
"""

import json
import queue
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agents import build_agents
from data_tool import download_dataset
from graph import SPEAKER_PREFIX_RE, STATUS_TAG_RE, AgentConfig, build_phase_graph, speak_once
from run_tools import DataPaths, RunTools

load_dotenv()  # finds the .env at the repo root

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
# (see agents.py's data_engineer_collecting), the budget is scaled up so the
# Data Analyst still gets roughly as many actual searching/downloading turns
# as before.
MAX_TURNS_COLLECT = 36
MAX_TURNS = 80  # safety net: a live demo shouldn't run forever if nobody stops it
MAX_RUNTIME_SECONDS = 20 * 60  # ...or 20 minutes, whichever comes first

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


STEP1B_GOAL = (
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
    "price-prediction objective for this project."
)
STEP2_GOAL = (
    "Decide together what real data would let us build this price-prediction "
    "model — which sources, which fields. We need individual, "
    "single-apartment-level records (one row per listing) — not pre-aggregated "
    "statistics (e.g. medians/percentiles by room count or district) — since a "
    "price-prediction model needs per-apartment examples to learn from."
)
STEP3_GOAL = (
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
    "individual-level-vs-aggregated call."
)
STEP4A_GOAL = (
    "Briefly discuss how you'll clean and store the downloaded data before doing "
    "it. Ground this in the REAL tool you actually have: store_to_database "
    "writes to a real local SQLite file via Python's stdlib sqlite3 — not "
    "PostgreSQL, MySQL, or any other system. Keep this planning short and "
    "concrete, tied to that real tool, not a hypothetical enterprise setup."
)
STEP4B_GOAL = (
    "Really clean the downloaded data, store it in the real SQLite database, "
    "and verify it with a real SQL query. Once that's verified, wrap up: you "
    "may note in ONE short clause that Exploratory Data Analysis (EDA) is "
    "the next step in the process — nothing more. Do NOT describe how you'd "
    "do EDA, do NOT name or discuss any modeling technique, algorithm, or "
    "statistical method (regression, decision trees, neural networks, etc.) "
    "— that's a separate, not-yet-built part of the process."
)

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
# Every real dataset file a run touches (downloaded, cleaned, the SQLite
# database, the fallback dataset) lives here — kept separate from the
# source files above it.
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DOWNLOAD_PATH = DATA_DIR / "downloaded_dataset.csv"
CLEANED_PATH = DATA_DIR / "cleaned_dataset.csv"
DB_PATH = DATA_DIR / "rental_data.db"
FALLBACK_PATH = DATA_DIR / "fallback_dataset.csv"
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


def _render_step3_result_lines(data: dict) -> list[str]:
    lines: list[str] = []
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
    return lines


def _render_step4_result_lines(data: dict) -> list[str]:
    lines: list[str] = []
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


def _render_phase_result_lines(step: int, data: dict) -> list[str]:
    """Compact, human-facing facts behind a step's tools — the same fields
    static/app.js's buildCollectingCard/buildPreparingCard already surface,
    so the saved file and the live UI agree on what matters."""
    if step == 3:
        return _render_step3_result_lines(data)
    if step == 4:
        return _render_step4_result_lines(data)
    return []


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


class PhaseSpec(NamedTuple):
    """What a phase needs beyond its participants — bundled into one value
    so `DemoRun.run_phase()` takes two arguments (agents, spec) instead of
    one per field."""

    step: int
    step_label: str
    sub_label: str
    goal: str
    has_tools: bool
    max_turns_override: int | None = None
    min_turns_override: int | None = None


class DemoRun:
    """One live run's state and step-by-step orchestration.

    Instantiated fresh per HTTP request (see `stream()` below) so runs
    never share state; `run()` is the entry point, called from a
    background thread while the FastAPI endpoint streams `self.q`.
    """

    def __init__(self, q: "queue.Queue"):
        self.q = q
        self.run_started_at = datetime.now()
        self.history: list[dict] = []  # every phase header and turn, saved once the run ends
        # Overwritten on every real exit path; this default only matters if
        # an exception somehow slips past the except block untouched.
        self.outcome: dict = {"status": "unknown"}

        self.turn_count = 0
        self.started_at = time.monotonic()
        self.transcript: list[dict] = []
        # Which step is currently active, so a stopped/timed-out outcome can
        # say where it happened rather than just that it happened.
        self.current_step = {"step": 1, "step_label": "Business objective"}
        self.phase_graph = build_phase_graph()  # one compiled graph, reused for every phase

        self.agents = build_agents()
        data_paths = DataPaths(download=DOWNLOAD_PATH, cleaned=CLEANED_PATH, db=DB_PATH)
        self.tools = RunTools(data_paths, on_progress=self._on_progress)
        self.tool_impls = self.tools.build_tool_impls()

    def _on_progress(self, stage: str):
        self.q.put(_sse("progress", {"stage": stage}))

    def _time_left(self) -> bool:
        return (time.monotonic() - self.started_at) < MAX_RUNTIME_SECONDS

    def _should_stop(self) -> bool:
        return self.turn_count >= MAX_TURNS or not self._time_left() or stop_event.is_set()

    def _stream_turn(self, speaker: str, text: str, used_tool: bool, step: int, step_label: str):
        self.turn_count += 1
        self.q.put(
            _sse(
                "progress",
                {"stage": f"Turn {self.turn_count}", "step": step, "step_label": step_label},
            )
        )
        self.q.put(_sse("turn", {"speaker": speaker, "text": text, "action": used_tool}))
        self.history.append(
            {"kind": "turn", "speaker": speaker, "text": text, "action": used_tool}
        )
        time.sleep(TURN_DELAY_SECONDS)

    def _speak_once(self, agent: AgentConfig, step: int, step_label: str):
        """One-off reply outside a full phase loop (the business-objective
        acknowledgment, the fallback-dataset announcement): speak, clean
        the reply, record it, stream it."""
        raw_reply, used_tool = speak_once(agent, self.transcript, self.tool_impls)
        clean_reply = STATUS_TAG_RE.sub("", raw_reply or "").strip()
        clean_reply = SPEAKER_PREFIX_RE.sub("", clean_reply)
        self.transcript.append({"speaker": agent.name, "text": clean_reply})
        self._stream_turn(agent.name, clean_reply, used_tool, step, step_label)

    def run_phase(self, agents: list[AgentConfig], spec: PhaseSpec):
        """Stream one phase's conversation loop (see graph.py) until its
        agents reach consensus, a turn cap is hit, or the run-level safety
        net / a manual Stop says to end early."""
        self.current_step["step"] = spec.step
        self.current_step["step_label"] = spec.step_label
        self.q.put(
            _sse(
                "phase_start",
                {
                    "step": spec.step,
                    "step_label": spec.step_label,
                    "sub_label": spec.sub_label,
                    "goal": spec.goal,
                },
            )
        )
        self.history.append(
            {
                "kind": "phase",
                "step": spec.step,
                "step_label": spec.step_label,
                "sub_label": spec.sub_label,
                "goal": spec.goal,
            }
        )
        self.transcript.append({"speaker": "system", "text": self._phase_instructions(spec)})

        if self._should_stop():
            return

        min_turns = spec.min_turns_override or (
            MIN_TURNS_ACTION if spec.has_tools else MIN_TURNS_DISCUSSION
        )
        max_turns = spec.max_turns_override or (
            MAX_TURNS_ACTION if spec.has_tools else MAX_TURNS_DISCUSSION
        )
        self._stream_phase_graph(agents, spec, min_turns, max_turns)

    @staticmethod
    def _phase_instructions(spec: PhaseSpec) -> str:
        return (
            f"Step {spec.step}/4 — {spec.step_label} ({spec.sub_label}). Goal: {spec.goal} "
            "Stay strictly on THIS goal — don't jump ahead to later steps (e.g. "
            "don't discuss cleaning approach or database design before data is "
            "even collected). Data ENGINEERING topics are fair game whenever "
            "relevant — data types, the database/table schema (the 'data "
            "model'), ingestion steps, keys, formats. What's OUT OF SCOPE for "
            "this whole demo is DATA ANALYSIS/MODELING — never name or discuss "
            "an analysis or predictive-modeling technique (outlier detection, "
            "scoring, statistics, regression, decision trees, neural networks, "
            "or any other algorithm) — that's a separate, not-yet-built part "
            "of the process; at most, note in passing that analysis comes "
            "later, with zero detail. If your peer's message drifts into "
            "something premature, don't follow along — redirect them back to "
            "this step's goal instead."
        )

    def _stream_phase_graph(
        self, agents: list[AgentConfig], spec: PhaseSpec, min_turns: int, max_turns: int
    ):
        # The graph itself only knows about THIS phase's own turn cap and
        # consensus (min_turns + everyone's status tag) — the run-level
        # safety net (MAX_TURNS turns / MAX_RUNTIME_SECONDS) and a manual
        # Stop are enforced here, by simply not asking the stream for a
        # next turn, re-checked after every completed turn.
        initial_state = {
            "transcript": self.transcript,
            "phase_agents": agents,
            "tool_impls": self.tool_impls,
            "turn_idx": 0,
            "turns_in_phase": 0,
            "min_turns": min_turns,
            "max_turns": max_turns,
            "ready": {a.name: False for a in agents},
            "pending_speaker": "",
            "pending_messages": [],
            "pending_ai_message": None,
            "final_text": "",
            "used_tool": False,
            "last_turn": None,
        }

        emitted = 0
        final_state = initial_state
        for state in self.phase_graph.stream(initial_state, stream_mode="values"):
            final_state = state
            if state["turns_in_phase"] > emitted and state["last_turn"]:
                turn = state["last_turn"]
                self._stream_turn(
                    turn["speaker"], turn["text"], turn["used_tool"], spec.step, spec.step_label
                )
                emitted = state["turns_in_phase"]
            if self._should_stop():
                break

        self.transcript[:] = final_state["transcript"]

    # --- Step 1: Business objective — fixed, not agent-decided ----------

    def _run_step1(self):
        business_objective = _build_business_objective()
        self.q.put(
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
        self.history.append(
            {
                "kind": "phase",
                "step": 1,
                "step_label": "Business objective",
                "sub_label": "",
                "goal": business_objective,
            }
        )
        self.transcript.append(
            {"speaker": self.agents.product_manager.name, "text": business_objective}
        )
        self._stream_turn(
            self.agents.product_manager.name, business_objective, False, 1, "Business objective"
        )
        self._speak_once(self.agents.data_analyst_notools, 1, "Business objective")

        # Before locking in the data requirements, have all three peers
        # briefly consider what OTHER objectives this same rental dataset
        # could serve — each from their own role's angle — then agree to
        # stick with the price-prediction objective. Round-robin across all
        # three (not just PM + one specialist) since this is about the
        # product as a whole, not one step's deliverable.
        if not self._should_stop():
            self.run_phase(
                [
                    self.agents.product_manager,
                    self.agents.data_analyst_notools,
                    self.agents.data_engineer_notools,
                ],
                PhaseSpec(
                    step=1,
                    step_label="Business objective",
                    sub_label="other objectives to consider",
                    goal=STEP1B_GOAL,
                    has_tools=False,
                    min_turns_override=3,
                ),
            )

    # --- Step 2: Defining appropriate data — discussion only ------------

    def _run_step2(self):
        self.run_phase(
            [self.agents.product_manager, self.agents.data_analyst_notools],
            PhaseSpec(
                step=2,
                step_label="Defining appropriate data",
                sub_label="discussion",
                goal=STEP2_GOAL,
                has_tools=False,
            ),
        )

    # --- Step 3: Collecting data — real tools ----------------------------

    def _run_step3(self):
        self.run_phase(
            [
                self.agents.product_manager,
                self.agents.data_analyst_with_tools,
                self.agents.data_engineer_collecting,
            ],
            PhaseSpec(
                step=3,
                step_label="Collecting data",
                sub_label="action",
                goal=STEP3_GOAL,
                has_tools=True,
                max_turns_override=MAX_TURNS_COLLECT,
            ),
        )

        # No individual-level dataset ever got confirmed within the search
        # budget — really download a known, always-available real dataset
        # (aggregated, not individual-level) as a guaranteed last resort,
        # rather than ending Step 3 with nothing. This runs regardless of
        # whether the loop above ended early (Stop / safety net), same as
        # the phase_done event right after it — Step 3 always reports what
        # actually happened.
        if not self.tools.dataset_ready:
            self._run_step3_fallback()

        step3_result = {
            "step": 3,
            "step_label": "Collecting data",
            "opendata": dict(self.tools.results["opendata"]),
            "download": dict(self.tools.results["download"]),
            "preview": dict(self.tools.results["download_preview"]),
        }
        self.q.put(_sse("phase_done", step3_result))
        self.history.append({"kind": "phase_result", "step": 3, "data": step3_result})

    def _run_step3_fallback(self):
        # Still a real HTTP download, same download_dataset() used for
        # every other Step 3 download.
        fallback_download = download_dataset(
            resource_url=FALLBACK_DATASET_URL,
            resource_format=FALLBACK_DATASET_FORMAT,
            out_path=str(FALLBACK_PATH),
            on_progress=self._on_progress,
        )
        if not fallback_download.get("success"):
            # Even the guaranteed fallback failed (e.g. a real network
            # issue) — fall through; the "incomplete" outcome below still
            # applies honestly.
            return

        self.tools.current_file["path"] = str(FALLBACK_PATH)
        self.tools.current_file["format"] = FALLBACK_DATASET_FORMAT
        self.tools.dataset_ready = True
        fallback_reason = (
            "it's aggregated (one row per district/room-count/year), not "
            "individual-apartment listings"
        )
        self.tools.results["download"] = {
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
        self.tools.capture_preview(
            "download_preview", self.tools.current_file["path"], self.tools.current_file["format"]
        )

        # The decision has to be voiced by an agent, not a silent system
        # switch — prime the Product Manager with the real facts and have
        # it actually say so, the same speak-then-stream pattern used for
        # Step 1's acknowledgment turn. The Product Manager (not the Data
        # Analyst) says this specifically so we don't hand a tool-bearing
        # agent a reason to call discard_dataset again on the file we just
        # downloaded.
        self.transcript.append(
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
        self._speak_once(self.agents.product_manager, 3, "Collecting data")

    # --- Step 4: Preparing & storing data — discuss, then real tools ----

    def _run_step4(self):
        self.run_phase(
            [self.agents.product_manager, self.agents.data_engineer_notools],
            PhaseSpec(
                step=4,
                step_label="Preparing & storing data",
                sub_label="planning",
                goal=STEP4A_GOAL,
                has_tools=False,
            ),
        )
        if not self._should_stop():
            self.run_phase(
                [self.agents.product_manager, self.agents.data_engineer_with_tools],
                PhaseSpec(
                    step=4,
                    step_label="Preparing & storing data",
                    sub_label="execution",
                    goal=STEP4B_GOAL,
                    has_tools=True,
                ),
            )
        step4_result = {
            "step": 4,
            "step_label": "Preparing & storing data",
            "profile": dict(self.tools.results["profile"]),
            "clean": dict(self.tools.results["clean"]),
            "store": dict(self.tools.results["store"]),
            "sql": dict(self.tools.results["sql"]),
            "sketch": dict(self.tools.results["sketch"]),
            "preview": dict(self.tools.results["clean_preview"]),
        }
        self.q.put(_sse("phase_done", step4_result))
        self.history.append({"kind": "phase_result", "step": 4, "data": step4_result})
        if self.tools.results["download"].get("fallback"):
            self.outcome = {
                "status": "completed_with_fallback",
                "message": (
                    "Completed all 4 steps using a fallback (aggregated/best-available) "
                    "dataset — no individual-apartment-level data was ever confirmed."
                ),
            }
        else:
            self.outcome = {"status": "completed", "message": "Completed all 4 steps."}
        self.q.put(_sse("done", self.outcome))

    def _finish_incomplete(self):
        # Step 3 ended without ever landing on genuine individual-level data
        # (everything found was aggregated/wrong-topic and got discarded, or
        # the search budget ran out first) — say so honestly instead of
        # faking step 4 against no real file.
        self.outcome = {
            "status": "incomplete",
            "message": (
                "No individual-apartment-level dataset could be confirmed within "
                "the search budget — every candidate turned out to be aggregated "
                "or off-topic and was discarded. Preparing & storing data was "
                "skipped since there's no real file to work with."
            ),
        }
        self.q.put(_sse("done", {"incomplete": True, **self.outcome}))

    def _finish_stopped_or_timed_out(self):
        # should_stop() was already true before step 3/4 even ran — either
        # the user clicked Stop, or the turn/time safety net kicked in.
        where = f"during Step {self.current_step['step']}/4 · {self.current_step['step_label']}"
        if stop_event.is_set():
            self.outcome = {"status": "stopped", "message": f"Stopped by the user {where}."}
        else:
            minutes = MAX_RUNTIME_SECONDS // 60
            self.outcome = {
                "status": "timed_out",
                "message": f"Hit the safety net ({MAX_TURNS} turns or {minutes} minutes) {where}.",
            }
        self.q.put(_sse("done", self.outcome))

    def run(self):
        """Run all four steps in order, once, then save the transcript."""
        try:
            # Each run starts genuinely fresh — no file from a previous run
            # can leak in and be mistaken for real data in this one.
            for stale_path in (DOWNLOAD_PATH, CLEANED_PATH, DB_PATH, FALLBACK_PATH):
                stale_path.unlink(missing_ok=True)

            self._run_step1()
            if not self._should_stop():
                self._run_step2()
            if not self._should_stop():
                self._run_step3()

            if not self._should_stop() and self.tools.dataset_ready:
                self._run_step4()
            elif not self._should_stop():
                self._finish_incomplete()
            else:
                self._finish_stopped_or_timed_out()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            # Surfaced to the browser instead of hanging. Named "app_error",
            # not "error" — EventSource treats a literal "error" SSE event
            # as indistinguishable from its own native connection-failure
            # event, so the frontend's genuine connection-lost handler was
            # silently stomping this message.
            self.outcome = {"status": "error", "message": str(exc)}
            self.q.put(_sse("app_error", {"message": str(exc)}))
        finally:
            # Saved regardless of how the run ended (finished, stopped, or
            # errored) so a partial conversation is never silently lost.
            _save_conversation_history(self.history, self.run_started_at, self.outcome)
            self.q.put(None)  # sentinel: stop the stream


def _run_demo(q: "queue.Queue"):
    DemoRun(q).run()


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

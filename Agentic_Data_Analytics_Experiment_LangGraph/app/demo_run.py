"""One live demo run: walks three peer agents — a Product Manager, a Data
Analyst, and a Data Engineer, none of them ranking above the others —
through the first four steps of the course's Data Analytics Process Model,
in order, once:

  1. Business objective    — fixed (given, not agent-decided)
  2. Defining appropriate data — discussion only (no tools yet)
  3. Collecting data        — agent-written scraper code (really run),
                               real open-data search/download tools
  4. Preparing & storing data — planning, then agent-written cleaning
                                 code and agent-written enrichment code
                                 (both really run), real SQLite storage +
                                 a real SQL query

Deliberately no analysis/interpretation happens here (that's the next,
not-yet-built part of the process model) — step 4 cleans the data and
enriches every listing with new columns, it doesn't derive insights. Turn-taking and tool-calling
are driven by a LangGraph `StateGraph` (see agents/graph.py): each phase
streams the compiled graph until its agents reach consensus (a status tag)
or a turn cap is hit.

Every event is pushed onto a queue as a Server-Sent-Events string; the
FastAPI endpoint in server.py streams that queue to the browser.
"""

import json
import queue
import shutil
import threading
import time
from datetime import datetime
from typing import NamedTuple

from agents import prompts
from agents.graph import (
    SPEAKER_PREFIX_RE,
    STATUS_TAG_RE,
    AgentConfig,
    build_phase_graph,
    speak_once,
)
from agents.personas import build_agents
from app.config import (
    CLEANED_PATH,
    CONVERSATION_HISTORY_DIR,
    DB_PATH,
    DOWNLOAD_PATH,
    ENRICHED_PATH,
    FALLBACK_DATASET_FORMAT,
    FALLBACK_DATASET_ORGANIZATION,
    FALLBACK_DATASET_PAGE_URL,
    FALLBACK_DATASET_TITLE,
    FALLBACK_DATASET_URL,
    FALLBACK_PATH,
    MAX_RUNTIME_SECONDS,
    MAX_TURNS,
    MAX_TURNS_ACTION,
    MAX_TURNS_COLLECT,
    MAX_TURNS_DISCUSSION,
    MAX_TURNS_PREP,
    MIN_TURNS_ACTION,
    MIN_TURNS_DISCUSSION,
    MIN_TURNS_ROUND_ROBIN,
    PREP_DIR,
    SCRAPED_PATH,
    SCRAPERS_DIR,
    STATIC_DIR,
    TURN_DELAY_SECONDS,
)
from reporting import transcript
from tools.opendata import download_dataset
from tools.preparation import preview_data, profile_data
from tools.run_tools import DataPaths, RunTools


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _save_conversation_history(history: list[dict], started_at: datetime, outcome: dict) -> None:
    """Save one run as both Markdown and HTML (see reporting/transcript.py) — the
    HTML version reuses the live page's own stylesheet, header, and process
    diagram, read fresh each time so an edit to any of them is picked up
    without a restart."""
    assets = transcript.PageAssets(
        css=(STATIC_DIR / "style.css").read_text(encoding="utf-8"),
        index_html=(STATIC_DIR / "index.html").read_text(encoding="utf-8"),
        svg_markup=(STATIC_DIR / "data_analytics_process_model.svg").read_text(encoding="utf-8"),
    )
    transcript.save(history, started_at, outcome, CONVERSATION_HISTORY_DIR, assets)


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

    Instantiated fresh per HTTP request (see app/server.py's `stream()`)
    so runs never share state; `run()` is the entry point, called from a
    background thread while the FastAPI endpoint streams `self.q`.
    `stop_event` is set by the frontend's Stop button (POST /api/stop).
    """

    def __init__(self, q: "queue.Queue", stop_event: threading.Event):
        self.q = q
        self.stop_event = stop_event
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

        data_paths = DataPaths(
            download=DOWNLOAD_PATH,
            scraped=SCRAPED_PATH,
            scrapers_dir=SCRAPERS_DIR,
            cleaned=CLEANED_PATH,
            db=DB_PATH,
            prep_dir=PREP_DIR,
            enriched=ENRICHED_PATH,
            history=CONVERSATION_HISTORY_DIR,
        )
        self.tools = RunTools(
            data_paths, on_progress=self._on_progress, on_artifact=self._on_artifact
        )
        self.agents = build_agents(
            analyst_working_notes=self.tools.scraper_working_notes,
            prep_working_notes=self.tools.prep_working_notes,
        )

    @property
    def tool_impls(self) -> dict:
        """Name -> callable for every real tool (see RunTools)."""
        return self.tools.build_tool_impls()

    def _on_progress(self, stage: str):
        self.q.put(_sse("progress", {"stage": stage}))

    def _on_artifact(self, kind: str, data: dict):
        """A scraper / preparation script the agent just wrote, a run of
        one that just finished, a real example shown to the class, or a
        look at earlier runs — shown inline in the chat and kept in the
        history."""
        self.q.put(_sse(kind, data))
        self.history.append({"kind": kind, "data": data})

    def _time_left(self) -> bool:
        return (time.monotonic() - self.started_at) < MAX_RUNTIME_SECONDS

    def _should_stop(self) -> bool:
        return self.turn_count >= MAX_TURNS or not self._time_left() or self.stop_event.is_set()

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
        """Stream one phase's conversation loop (see agents/graph.py) until its
        agents reach consensus, a turn cap is hit, or the run-level safety
        net / a manual Stop says to end early."""
        self.current_step["step"] = spec.step
        self.current_step["step_label"] = spec.step_label
        self.tools.teaching.new_phase()
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
        self.transcript.append(
            {
                "speaker": "system",
                "text": prompts.phase_instructions(
                    spec.step, spec.step_label, spec.sub_label, spec.goal
                ),
            }
        )

        if self._should_stop():
            return

        min_turns = spec.min_turns_override or (
            MIN_TURNS_ACTION if spec.has_tools else MIN_TURNS_DISCUSSION
        )
        max_turns = spec.max_turns_override or (
            MAX_TURNS_ACTION if spec.has_tools else MAX_TURNS_DISCUSSION
        )
        self._stream_phase_graph(agents, spec, min_turns, max_turns)

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
            "team_notes": [],
            "recent_turns": [],
            "last_turn": None,
        }

        emitted = 0
        final_state = initial_state
        for state in self.phase_graph.stream(initial_state, stream_mode="values"):
            final_state = state
            if state["turns_in_phase"] > emitted and state["last_turn"]:
                turn = state["last_turn"]
                if turn["text"]:  # a bare status tag isn't worth a bubble
                    self._stream_turn(
                        turn["speaker"], turn["text"], turn["used_tool"], spec.step, spec.step_label
                    )
                emitted = state["turns_in_phase"]
            if self._should_stop():
                break

        self.transcript[:] = final_state["transcript"]

    # --- Step 1: Business objective — fixed, not agent-decided ----------

    def _run_step1(self):
        business_objective = prompts.build_business_objective()
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
                    goal=prompts.STEP1B_GOAL,
                    has_tools=False,
                    min_turns_override=MIN_TURNS_ROUND_ROBIN,
                ),
            )

        # A short privacy/legal check before any data collection starts —
        # deliberately placed here (not Step 3) so the legal call is made
        # up front, not improvised mid-scrape once a block is already
        # staring the agents in the face.
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
                    sub_label="data privacy & legal check",
                    goal=prompts.STEP1C_GOAL,
                    has_tools=False,
                    min_turns_override=MIN_TURNS_ROUND_ROBIN,
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
                goal=prompts.STEP2_GOAL,
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
                goal=prompts.STEP3_GOAL,
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
                "text": prompts.fallback_announcement(
                    FALLBACK_DATASET_TITLE, FALLBACK_DATASET_ORGANIZATION, fallback_reason
                ),
            }
        )
        self._speak_once(self.agents.product_manager, 3, "Collecting data")

    # --- Step 4: Preparing & storing data — plan, clean, enrich, store --

    def _brief_on_dataset(self):
        """Open Step 4 with the real facts about the collected file, so the
        planning talk is about the actual columns, not imagined ones."""
        path, fmt = self.tools.current_file["path"], self.tools.current_file["format"]
        profile = profile_data(path=path, data_format=fmt)
        preview = preview_data(path=path, data_format=fmt, n=3)
        if profile.get("error") or preview.get("error"):
            return
        self.tools.results["profile"] = profile
        self.transcript.append(
            {"speaker": "system", "text": prompts.dataset_briefing(profile, preview)}
        )

    def _run_step4(self):
        step_label = "Preparing & storing data"
        self._brief_on_dataset()
        self.run_phase(
            [
                self.agents.product_manager,
                self.agents.data_engineer_notools,
                self.agents.data_analyst_notools,
            ],
            PhaseSpec(
                step=4,
                step_label=step_label,
                sub_label="planning",
                goal=prompts.STEP4A_GOAL,
                has_tools=False,
                min_turns_override=MIN_TURNS_ROUND_ROBIN,
            ),
        )
        # Cleaning, then enrichment: each an agent-written script really
        # run on the current dataset, owned by one agent and reviewed by
        # the other (see agents/prompts.py). Enrichment is per apartment,
        # so it's skipped on the aggregated fallback dataset.
        if not self._should_stop():
            self.tools.start_prep_stage("clean")
            self.run_phase(
                [
                    self.agents.product_manager,
                    self.agents.data_engineer_cleaning,
                    self.agents.data_analyst_reviewing_cleaning,
                ],
                PhaseSpec(
                    step=4,
                    step_label=step_label,
                    sub_label="cleaning",
                    goal=prompts.STEP4B_GOAL,
                    has_tools=True,
                    max_turns_override=MAX_TURNS_PREP,
                ),
            )
        if not self._should_stop() and not self.tools.results["download"].get("fallback"):
            self.tools.start_prep_stage("enrich")
            self.run_phase(
                [
                    self.agents.product_manager,
                    self.agents.data_analyst_enriching,
                    self.agents.data_engineer_reviewing_enrichment,
                ],
                PhaseSpec(
                    step=4,
                    step_label=step_label,
                    sub_label="enrichment",
                    goal=prompts.STEP4C_GOAL,
                    has_tools=True,
                    max_turns_override=MAX_TURNS_PREP,
                ),
            )
        self.tools.end_prep_stage()
        if not self._should_stop():
            self.run_phase(
                [self.agents.product_manager, self.agents.data_engineer_storing],
                PhaseSpec(
                    step=4,
                    step_label=step_label,
                    sub_label="storing",
                    goal=prompts.STEP4D_GOAL,
                    has_tools=True,
                ),
            )
        step4_result = {
            "step": 4,
            "step_label": step_label,
            "profile": dict(self.tools.results["profile"]),
            "clean": dict(self.tools.results["clean"]),
            "enrich": dict(self.tools.results["enrich"]),
            "store": dict(self.tools.results["store"]),
            "sql": dict(self.tools.results["sql"]),
            "sketch": dict(self.tools.results["sketch"]),
            "preview": dict(self.tools.results["prepared_preview"]),
        }
        self.q.put(_sse("phase_done", step4_result))
        self.history.append({"kind": "phase_result", "step": 4, "data": step4_result})
        if self._should_stop() and not self.tools.results["store"]:
            self._finish_stopped_or_timed_out()
        elif self.tools.results["download"].get("fallback"):
            self.outcome = {
                "status": "completed_with_fallback",
                "message": (
                    "Completed all 4 steps using a fallback (aggregated/best-available) "
                    "dataset — no individual-apartment-level data was ever confirmed."
                ),
            }
            self.q.put(_sse("done", self.outcome))
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
        if self.stop_event.is_set():
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
            for stale_path in (
                DOWNLOAD_PATH, SCRAPED_PATH, CLEANED_PATH, ENRICHED_PATH, DB_PATH, FALLBACK_PATH
            ):
                stale_path.unlink(missing_ok=True)
            shutil.rmtree(SCRAPERS_DIR, ignore_errors=True)
            shutil.rmtree(PREP_DIR, ignore_errors=True)

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

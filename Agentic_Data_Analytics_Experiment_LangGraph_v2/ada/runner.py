"""Start / resume / stop / approve runs. Each run executes the compiled graph in
a background thread; state is checkpointed to SQLite after every node, so a run
interrupted by a crash or restart resumes from its last completed phase."""
from __future__ import annotations

import sqlite3
import subprocess
import threading
import time
import uuid
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ada.budget import BudgetTracker
from ada.config import PROJECT_ROOT, Config, load_env
from ada.context import RunContext
from ada.events import EventStore, default_store
from ada.graph.build import build_graph
from ada.graph.state import initial_state
from ada.paths import checkpoint_db_path
from ada.store import RunStore
from ada.vault import HoldoutVault

_saver_lock = threading.Lock()
_saver: SqliteSaver | None = None


def checkpointer() -> SqliteSaver:
    global _saver
    with _saver_lock:
        if _saver is None:
            conn = sqlite3.connect(checkpoint_db_path(), check_same_thread=False, timeout=30)
            conn.execute("PRAGMA journal_mode=WAL")
            _saver = SqliteSaver(conn)
            _saver.setup()
        return _saver


def system_version() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT, capture_output=True,
                             text=True, timeout=10).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "."], cwd=PROJECT_ROOT, capture_output=True,
                               text=True, timeout=10).stdout.strip()
        return f"{sha}{'+dirty' if dirty else ''}" or "unversioned"
    except Exception:
        return "unversioned"


def _overrides(options: dict[str, Any]) -> dict[str, Any]:
    ov: dict[str, Any] = dict(options.get("config_overrides") or {})
    if options.get("budgets"):
        ov.setdefault("budgets", {}).update(options["budgets"])
    if "human_approval" in options:
        ov["human_approval"] = bool(options["human_approval"])
    if options.get("stub_delay") is not None:
        ov["stub"] = {"delay": float(options["stub_delay"])}
    return ov


class RunManager:
    def __init__(self, events: EventStore | None = None):
        load_env()
        self.events = events or default_store()
        self._threads: dict[str, threading.Thread] = {}
        self._contexts: dict[str, RunContext] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    def _context(self, run_id: str, options: dict[str, Any], budget_snapshot: dict[str, Any] | None) -> RunContext:
        cfg = Config.load(_overrides(options))
        store = RunStore(run_id).init()
        return RunContext(run_id=run_id, cfg=cfg, events=self.events, budget=BudgetTracker(cfg, budget_snapshot),
                          store=store, vault=HoldoutVault(run_id, cfg), mode=options.get("mode", "real"),
                          offline=bool(options.get("offline", False)), llm_client=options.get("_llm_client"))

    def start(self, objective: str, region: str | None = None, options: dict[str, Any] | None = None,
              *, background: bool = True, run_id: str | None = None) -> str:
        options = dict(options or {})
        run_id = run_id or time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        persisted = {k: v for k, v in options.items() if not k.startswith("_")}
        self.events.create_run(run_id, objective, region, options.get("mode", "real"), persisted, system_version())
        ctx = self._context(run_id, options, None)
        ctx.emit("transition", f"run started: {objective[:120]}", node=None, agent="orchestrator",
                 payload={"from": "start", "to": "business_objectives", "kind": "start", "objective": objective,
                          "region": region, "options": persisted})
        state = initial_state(run_id, objective, region, persisted)
        self._launch(ctx, state, background)
        return run_id

    def resume(self, run_id: str, *, background: bool = True, resume_value: Any = None) -> str:
        run = self.events.get_run(run_id)
        if run is None:
            raise KeyError(run_id)
        options = dict(run["options"])
        graph_state = self._graph(self._context(run_id, options, None)).get_state(self._thread(run_id))
        snapshot = (graph_state.values or {}).get("budget") if graph_state else None
        ctx = self._context(run_id, options, snapshot)
        self.events.update_run(run_id, status="running")
        ctx.emit("transition", "run resumed", agent="orchestrator",
                 payload={"from": "resume", "to": (graph_state.values or {}).get("next") if graph_state else None,
                          "kind": "resume"})
        payload = Command(resume=resume_value) if resume_value is not None else None
        self._launch(ctx, payload, background)
        return run_id

    def approve(self, run_id: str, next_node: str | None = None, note: str = "") -> str:
        return self.resume(run_id, resume_value={"next_node": next_node, "note": note})

    def stop(self, run_id: str) -> bool:
        with self._lock:
            ctx = self._contexts.get(run_id)
        if ctx is None:
            return False
        ctx.stop_requested.set()
        ctx.emit("message", "stop requested — finishing the current step, then presenting results",
                 agent="orchestrator")
        return True

    def is_active(self, run_id: str) -> bool:
        with self._lock:
            t = self._threads.get(run_id)
        return bool(t and t.is_alive())

    def pending_interrupt(self, run_id: str) -> Any:
        run = self.events.get_run(run_id)
        if not run:
            return None
        ctx = self._context(run_id, dict(run["options"]), None)
        snap = self._graph(ctx).get_state(self._thread(run_id))
        for task in getattr(snap, "tasks", ()) or ():
            for intr in getattr(task, "interrupts", ()) or ():
                return intr.value
        return None

    # ------------------------------------------------------------------
    @staticmethod
    def _thread(run_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": run_id}, "recursion_limit": 400}

    @staticmethod
    def _graph(ctx: RunContext) -> Any:
        return build_graph(ctx, checkpointer=checkpointer())

    def _launch(self, ctx: RunContext, payload: Any, background: bool) -> None:
        with self._lock:
            self._contexts[ctx.run_id] = ctx
        if not background:
            self._execute(ctx, payload)
            return
        t = threading.Thread(target=self._execute, args=(ctx, payload), name=f"run-{ctx.run_id}", daemon=True)
        with self._lock:
            self._threads[ctx.run_id] = t
        t.start()

    def _execute(self, ctx: RunContext, payload: Any) -> None:
        graph = self._graph(ctx)
        config = self._thread(ctx.run_id)
        try:
            result = graph.invoke(payload, config)
            snap = graph.get_state(config)
            if snap.next:   # paused on an interrupt (human approval)
                self.events.update_run(ctx.run_id, status="awaiting_approval")
                ctx.emit("gate", "waiting for human approval", agent="orchestrator",
                         payload={"kind": "awaiting_approval", "interrupt": self.pending_interrupt(ctx.run_id)})
                return
            status = result.get("status", "completed")
            final = result.get("final") or {}
            self.events.update_run(ctx.run_id, status=status, summary={
                "current": "end", "visits": result.get("visits"), "loopbacks": result.get("loopbacks"),
                "budget": {k: result.get("budget", {}).get(k) for k in ("usd", "total_tokens", "elapsed_seconds")},
                "final": final, "stop_reason": result.get("stop_reason"),
                "validation": (result.get("validation") or {}).get("metrics"),
                "objective_spec": result.get("objective_spec"),
            })
        except Exception as exc:
            self.events.update_run(ctx.run_id, status="failed")
            ctx.emit("error", f"run crashed: {type(exc).__name__}: {exc} — resumable from the last checkpoint",
                     agent="orchestrator")
            raise
        finally:
            with self._lock:
                self._contexts.pop(ctx.run_id, None)

    def final_state(self, run_id: str) -> dict[str, Any]:
        run = self.events.get_run(run_id) or {"options": {}}
        ctx = self._context(run_id, dict(run["options"]), None)
        snap = self._graph(ctx).get_state(self._thread(run_id))
        return dict(snap.values or {})

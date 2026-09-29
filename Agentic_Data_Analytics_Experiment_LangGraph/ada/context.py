"""Per-run services shared by all nodes (not part of the checkpointed state)."""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from ada.budget import BudgetTracker
from ada.config import Config
from ada.events import EventStore
from ada.llm import LLM
from ada.sandbox.executor import Sandbox
from ada.store import RunStore
from ada.vault import HoldoutVault


@dataclass
class RunContext:
    run_id: str
    cfg: Config
    events: EventStore
    budget: BudgetTracker
    store: RunStore
    vault: HoldoutVault
    mode: str = "real"                 # real | stub
    offline: bool = False
    stop_requested: threading.Event = field(default_factory=threading.Event)
    llm_client: Any = None             # injectable for tests
    _llm: LLM | None = None
    _sandbox: Sandbox | None = None
    _stop_checked_at: float = 0.0

    def should_stop(self) -> bool:
        """True once a stop was requested — in this process or (via the database) from any other."""
        if self.stop_requested.is_set():
            return True
        now = time.monotonic()
        if now - self._stop_checked_at >= 2.0:
            self._stop_checked_at = now
            if self.events.stop_requested(self.run_id):
                self.stop_requested.set()
        return self.stop_requested.is_set()

    @property
    def llm(self) -> LLM:
        if self._llm is None:
            self._llm = LLM(self.cfg, self.budget, self.events, self.run_id, client=self.llm_client)
        return self._llm

    @property
    def sandbox(self) -> Sandbox:
        if self._sandbox is None:
            self._sandbox = Sandbox(self.cfg)
        return self._sandbox

    def emit(self, type: str, summary: str, *, node: str | None = None, agent: str | None = None,
             payload: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.events.emit(self.run_id, type, summary, node=node, agent=agent, payload=payload)

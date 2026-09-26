"""Per-run services shared by all nodes (not part of the checkpointed state)."""
from __future__ import annotations

import threading
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

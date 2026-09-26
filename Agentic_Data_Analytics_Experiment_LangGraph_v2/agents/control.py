"""Control agents: the skeptical Critic and the Supervisor that picks among the
edges a gate allows. Neither can widen the set of allowed edges."""
from __future__ import annotations

import json
from typing import Any

from agents.base import Agent
from ada.schemas import Critique, SupervisorDecision


class CriticAgent(Agent):
    name = "CriticAgent"
    role = "critic"
    prompt = "critic"
    tools = ["list_files", "read_file", "preview_table"]
    output_model = Critique

    def max_steps(self) -> int:
        return 6

    def review(self, *, phase: str, output: dict[str, Any] | None, checks: list[dict[str, Any]],
               evidence: str, allowed_routes: list[str]) -> Critique:
        task = (
            f"Phase under review: {phase}\n\n"
            f"Agent output:\n{json.dumps(output, indent=1, default=str)[:6000]}\n\n"
            f"Deterministic check results:\n{json.dumps(checks, indent=1, default=str)[:4000]}\n\n"
            f"Evidence (code the agent ran, artifacts):\n{evidence[:9000]}\n\n"
            f"If this phase must be redone upstream, suggested_route must be one of: {allowed_routes or 'none'}."
        )
        return self.run(task)  # type: ignore[return-value]


class SupervisorAgent(Agent):
    name = "Supervisor"
    role = "supervisor"
    prompt = "supervisor"
    tools: list[str] = []
    output_model = SupervisorDecision

    def max_steps(self) -> int:
        return 3

    def choose(self, *, node: str, gate: dict[str, Any], state_summary: dict[str, Any]) -> SupervisorDecision:
        task = (
            f"Phase just finished: {node}\nGate result:\n{json.dumps(gate, indent=1, default=str)}\n\n"
            f"Run state:\n{json.dumps(state_summary, indent=1, default=str)[:6000]}\n\n"
            f"Choose next_node from exactly these options: {gate['options']}."
        )
        return self.run(task)  # type: ignore[return-value]

"""LangGraph state. Holds pointers and compact summaries only — large data
stays in the run workspace."""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class RunState(TypedDict, total=False):
    run_id: str
    objective: str
    region: Optional[str]
    options: dict[str, Any]
    objective_spec: Optional[dict[str, Any]]
    phase_outputs: dict[str, dict[str, Any]]      # latest agent output per phase (compact)
    artifacts: dict[str, str]                      # name -> workspace-relative path
    phase_history: list[dict[str, Any]]
    gate_results: list[dict[str, Any]]
    visits: dict[str, int]
    consecutive_failures: dict[str, int]
    loopbacks: int
    edge_counts: dict[str, int]
    guidance: dict[str, list[str]]
    budget: dict[str, Any]
    validation: Optional[dict[str, Any]]           # harness metrics of the current best model
    best_model: Optional[dict[str, Any]]
    holdout_info: Optional[dict[str, Any]]
    final: Optional[dict[str, Any]]
    next: str
    status: str
    stop_reason: Optional[str]
    last_node: Optional[str]


def initial_state(run_id: str, objective: str, region: str | None, options: dict[str, Any]) -> RunState:
    return RunState(
        run_id=run_id, objective=objective, region=region, options=options, objective_spec=None,
        phase_outputs={}, artifacts={}, phase_history=[], gate_results=[], visits={},
        consecutive_failures={}, loopbacks=0, edge_counts={}, guidance={}, budget={}, validation=None,
        best_model=None, holdout_info=None, final=None, next="business_objectives", status="running",
        stop_reason=None, last_node=None,
    )

"""LangGraph wiring. Each process phase is a node; after the phase's work the
node runs the gate (deterministic checks + critic), lets the Supervisor pick one
of the edges the gate allows, and records the decision. Conditional edges from
each node are restricted to `topology.allowed_targets(node)`.

A phase's work is cached per visit in the workspace (.cache/<node>_<visit>.json),
so re-entering a node after an interrupt (human approval) or a crash does not
repeat finished agent work.
"""
from __future__ import annotations

import traceback
from typing import Any, Callable

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ada.budget import BudgetExceeded
from ada.context import RunContext
from ada.gates.checks import fail
from ada.gates.gate import GateResult, decide
from ada.graph import phases as real_phases
from ada.graph.phases import PhaseOutcome
from ada.graph.state import RunState
from ada.graph.stub import stub_choice, stub_phase
from ada.graph.topology import (AGENTS, END_NODE, FEEDBACK, NODES, START_NODE, allowed_targets, edge_kind)
from agents.base import AgentFailed, RunStopped
from agents.control import CriticAgent, SupervisorAgent

PhaseFn = Callable[[RunContext, dict[str, Any], str], PhaseOutcome]


def _merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    out.update(extra)
    return out


def _state_summary(state: dict[str, Any], ctx: RunContext) -> dict[str, Any]:
    return {
        "visits": state.get("visits"), "loopbacks": state.get("loopbacks"),
        "recent_gates": [{k: g.get(k) for k in ("node", "decision", "reasons", "hard_failures")}
                         for g in (state.get("gate_results") or [])[-5:]],
        "phase_summaries": {h["node"]: h.get("summary") for h in (state.get("phase_history") or [])},
        "validation": (state.get("validation") or {}).get("metrics"),
        "objective_spec": {k: (state.get("objective_spec") or {}).get(k) for k in ("task_type", "primary_metric", "success_threshold")},
        "budget_used": {k: ctx.budget.snapshot().get(k) for k in ("usd", "total_tokens", "elapsed_seconds")},
        "eda_output": (state.get("phase_outputs") or {}).get("eda"),
    }


def make_node(node: str, ctx: RunContext, phase_fn: PhaseFn) -> Callable[[RunState], dict[str, Any]]:
    stub = ctx.mode == "stub"

    def run(state: RunState) -> dict[str, Any]:
        visits = dict(state.get("visits") or {})
        visits[node] = visits.get(node, 0) + 1
        visit = visits[node]
        st = _merge(state, {"visits": visits})
        cache_rel = f".cache/{node}_{visit}.json"
        cached = ctx.store.read_json(cache_rel) or {}

        # ---- stop / budget: go straight to present_results ------------------------------
        stop_reason = state.get("stop_reason")
        if node != END_NODE and not stop_reason:
            if ctx.stop_requested.is_set():
                stop_reason = "stopped by user"
            else:
                stop_reason = ctx.budget.exhausted_reason()
        if node != END_NODE and stop_reason:
            ctx.emit("gate", f"{node}: escalate — {stop_reason}", node=node, agent="orchestrator",
                     payload={"node": node, "decision": "escalate", "options": [END_NODE], "reasons": [stop_reason]})
            return _route(ctx, st, node, END_NODE, f"escalate: {stop_reason}", "", None,
                          extra={"stop_reason": stop_reason, "visits": state.get("visits") or {}})

        # ---- phase work (cached per visit) --------------------------------------------------
        if "outcome" in cached:
            outcome = PhaseOutcome.from_cache(cached["outcome"])
        else:
            outcome = _run_phase(ctx, st, node, phase_fn)
            if outcome is None:   # budget ran out mid-phase
                reason = ctx.budget.exhausted_reason() or "budget exhausted"
                return _route(ctx, st, node, END_NODE, f"escalate: {reason}", "", None,
                              extra={"stop_reason": reason})
            cached = {"outcome": outcome.to_cache()}
            ctx.store.write_json(cache_rel, cached)
        st = _merge(st, _apply_updates(st, outcome.updates))
        phase_outputs = dict(st.get("phase_outputs") or {})
        if outcome.output is not None:
            phase_outputs[node] = outcome.output
        st["phase_outputs"] = phase_outputs

        if node == END_NODE:
            ctx.emit("transition", "run finished", node=node, agent="orchestrator",
                     payload={"from": node, "to": "end", "kind": "end"})
            status = "completed" if not st.get("stop_reason") else (
                "stopped" if st.get("stop_reason") == "stopped by user" else "budget_exhausted")
            return {**_keep(st), "next": "__end__", "status": status, "last_node": node,
                    "budget": ctx.budget.snapshot()}

        # ---- gate ----------------------------------------------------------------------
        if "gate" in cached:
            gate = GateResult(**cached["gate"])
        else:
            critique = None
            hard = [c for c in outcome.checks if not c.passed and c.severity == "error"]
            if not stub and not hard and outcome.output is not None:
                critique = _critic(ctx, node, outcome)
            gate = decide(node, outcome.checks, critique, st, ctx.cfg)
            cached["gate"] = gate.to_dict()
            cached["critique"] = critique
            ctx.store.write_json(cache_rel, cached)
            ctx.emit("gate", f"{node}: {gate.decision} → options {gate.options}", node=node, agent="Gate",
                     payload={**gate.to_dict(), "checks": [c.to_dict() for c in outcome.checks], "critique": critique,
                              "visit": visit})

        # ---- supervisor ----------------------------------------------------------------
        if "decision" in cached:
            decision = cached["decision"]
        else:
            decision = _supervise(ctx, node, gate, st, stub)
            cached["decision"] = decision
            ctx.store.write_json(cache_rel, cached)
        if ctx.cfg.get("human_approval"):
            answer = interrupt({"node": node, "gate": gate.to_dict(), "proposed": decision["next_node"],
                                "rationale": decision["rationale"]})
            if isinstance(answer, dict) and answer.get("next_node") in gate.options:
                decision = {**decision, "next_node": answer["next_node"],
                            "rationale": f"human: {answer.get('note') or 'approved'} (proposed {decision['next_node']})"}
        guidance = _guidance(gate, cached.get("critique"), decision, node, outcome)
        if node == "prepare_store" and gate.decision == "pass" and not stub:
            # orchestrator action: lock the holdout (first pass), refresh splits, store in DuckDB
            st = _merge(st, _apply_updates(st, real_phases.after_prepare_pass(ctx, st)))
        return _route(ctx, st, node, decision["next_node"], decision["rationale"], guidance, gate,
                      summary=outcome.summary)

    return run


def _run_phase(ctx: RunContext, st: dict[str, Any], node: str, phase_fn: PhaseFn) -> PhaseOutcome | None:
    try:
        return phase_fn(ctx, st, node)
    except BudgetExceeded as exc:
        ctx.emit("budget", f"budget exhausted during {node}: {exc.reason}", node=node, agent="orchestrator",
                 payload={"exhausted": True, "reason": exc.reason, "totals": ctx.budget.snapshot()})
        return None
    except RunStopped:
        ctx.stop_requested.set()
        return None
    except AgentFailed as exc:
        ctx.emit("error", str(exc), node=node, agent=AGENTS[node])
        return PhaseOutcome(None, [fail("agent_completed", str(exc))], f"agent failed: {exc}", error=str(exc))
    except Exception as exc:  # unexpected errors become a failed gate, not a crashed run
        ctx.emit("error", f"{node} crashed: {type(exc).__name__}: {exc}", node=node, agent="orchestrator",
                 payload={"traceback": traceback.format_exc()[-4000:]})
        return PhaseOutcome(None, [fail("phase_completed", f"{type(exc).__name__}: {exc}"[:500])],
                            f"error: {exc}", error=str(exc))


def _critic(ctx: RunContext, node: str, outcome: PhaseOutcome) -> dict[str, Any] | None:
    try:
        critique = CriticAgent(ctx, node).review(
            phase=node, output=outcome.output, checks=[c.to_dict() for c in outcome.checks],
            evidence=outcome.evidence, allowed_routes=FEEDBACK[node])
        data = critique.model_dump()
        ctx.emit("message", f"Critic {data['score']:.1f}/10 ({data['verdict']}): " +
                 ("; ".join(data["issues"][:3]) or "no issues"), node=node, agent="CriticAgent",
                 payload={"kind": "critique", **data})
        return data
    except (AgentFailed, BudgetExceeded) as exc:
        ctx.emit("error", f"critic unavailable: {exc}", node=node, agent="CriticAgent")
        return None


def _supervise(ctx: RunContext, node: str, gate: GateResult, st: dict[str, Any], stub: bool) -> dict[str, Any]:
    options = gate.options
    if len(options) == 1:
        return {"next_node": options[0], "rationale": f"{gate.decision}: {'; '.join(gate.reasons)}",
                "guidance_for_next_agent": ""}
    if stub:
        choice = stub_choice(node, options, st)
        return {"next_node": choice, "rationale": f"{gate.decision}; supervisor (stub) chose {choice}",
                "guidance_for_next_agent": ""}
    try:
        d = SupervisorAgent(ctx, node).choose(node=node, gate=gate.to_dict(), state_summary=_state_summary(st, ctx))
        if d.next_node in options:
            ctx.emit("message", f"Supervisor → {d.next_node}: {d.rationale}", node=node, agent="Supervisor",
                     payload={"kind": "supervisor", **d.model_dump(), "options": options})
            return d.model_dump()
        ctx.emit("error", f"supervisor chose disallowed edge {d.next_node!r}; using {options[0]}", node=node,
                 agent="Supervisor")
    except (AgentFailed, BudgetExceeded) as exc:
        ctx.emit("error", f"supervisor unavailable ({exc}); using first allowed option", node=node, agent="Supervisor")
    return {"next_node": options[0], "rationale": f"{gate.decision}: default route", "guidance_for_next_agent": ""}


def _guidance(gate: GateResult, critique: dict[str, Any] | None, decision: dict[str, Any], node: str,
              outcome: PhaseOutcome) -> str:
    parts = []
    if gate.decision != "pass":
        if gate.hard_failures:
            parts.append(f"[{node} gate] failed checks: " + "; ".join(gate.hard_failures)[:900])
        if critique and critique.get("feedback_for_agent"):
            parts.append(f"[critic on {node}] {critique['feedback_for_agent'][:900]}")
    if node in ("eda", "modeling") and outcome.output:
        req = outcome.output.get("requested_changes") or []
        if req:
            parts.append(f"[{node} requests] " + "; ".join(req)[:900])
    if decision.get("guidance_for_next_agent"):
        parts.append(f"[supervisor] {decision['guidance_for_next_agent'][:900]}")
    if gate.warnings:
        parts.append(f"[{node} warnings] " + "; ".join(gate.warnings)[:600])
    return "\n".join(parts)


def _apply_updates(st: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    out = dict(updates)
    if "artifacts" in updates:
        out["artifacts"] = {**(st.get("artifacts") or {}), **updates["artifacts"]}
    return out


def _keep(st: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in st.items() if k in RunState.__annotations__}


def _route(ctx: RunContext, st: dict[str, Any], node: str, target: str, rationale: str, guidance: str,
           gate: GateResult | None, *, summary: str = "", extra: dict[str, Any] | None = None) -> dict[str, Any]:
    if target not in allowed_targets(node):
        raise RuntimeError(f"illegal transition {node} -> {target}")
    kind = edge_kind(node, target)
    loopbacks = int(st.get("loopbacks", 0)) + (1 if kind == "feedback" else 0)
    edge_counts = dict(st.get("edge_counts") or {})
    key = f"{node}->{target}"
    edge_counts[key] = edge_counts.get(key, 0) + 1
    fails = dict(st.get("consecutive_failures") or {})
    fails[node] = 0 if gate is None or gate.decision == "pass" else fails.get(node, 0) + 1
    guidance_map = {k: list(v) for k, v in (st.get("guidance") or {}).items()}
    if target != node:
        guidance_map[node] = []           # the phase is done with its feedback
    if guidance:
        guidance_map.setdefault(target, []).append(guidance)
    history = list(st.get("phase_history") or [])
    history.append({"node": node, "visit": (st.get("visits") or {}).get(node), "decision": gate.decision if gate else "escalate",
                    "critic_score": gate.critic_score if gate else None, "next": target, "rationale": rationale[:500],
                    "summary": summary})
    gates = list(st.get("gate_results") or [])
    if gate:
        gates.append(gate.to_dict())
    ctx.emit("transition", f"{node} → {target} ({kind})", node=node, agent="Supervisor",
             payload={"from": node, "to": target, "kind": kind, "count": edge_counts[key], "rationale": rationale,
                      "decision": gate.decision if gate else "escalate"})
    snap = ctx.budget.snapshot()
    ctx.events.update_run(ctx.run_id, summary={"current": target, "visits": st.get("visits"), "loopbacks": loopbacks,
                                               "budget": {k: snap[k] for k in ("usd", "total_tokens", "elapsed_seconds")}})
    out = {**_keep(st), "next": target, "loopbacks": loopbacks, "edge_counts": edge_counts,
           "consecutive_failures": fails, "guidance": guidance_map, "phase_history": history,
           "gate_results": gates, "budget": snap, "last_node": node}
    if extra:
        out.update(extra)
    return out


def build_graph(ctx: RunContext, checkpointer: Any = None) -> Any:
    phase_fns: dict[str, PhaseFn] = {n: stub_phase for n in NODES} if ctx.mode == "stub" else dict(real_phases.PHASES)
    g = StateGraph(RunState)
    for node in NODES:
        g.add_node(node, make_node(node, ctx, phase_fns[node]))
    g.add_edge(START, START_NODE)
    for node in NODES:
        if node == END_NODE:
            g.add_edge(END_NODE, END)
            continue
        targets = allowed_targets(node)
        g.add_conditional_edges(node, _router(node), {t: t for t in targets})
    return g.compile(checkpointer=checkpointer)


def _router(node: str) -> Callable[[RunState], str]:
    def route(state: RunState) -> str:
        target = state.get("next")
        if target not in allowed_targets(node):
            raise RuntimeError(f"router: {node} -> {target!r} is not an allowed edge")
        return target
    route.__name__ = f"route_from_{node}"
    return route

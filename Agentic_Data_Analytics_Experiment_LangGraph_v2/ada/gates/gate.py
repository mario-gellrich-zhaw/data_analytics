"""Gate decision: deterministic checks + critic score -> pass | retry_same_phase |
route_back(<allowed node>) | escalate, plus the list of edges the Supervisor may
choose from. Protected code: the improver cannot touch it."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from ada.config import Config
from ada.gates.checks import CheckResult
from ada.graph.topology import END_NODE, FEEDBACK, FORWARD, PASS_EXTRA


@dataclass
class GateResult:
    node: str
    decision: str                       # pass | retry_same_phase | route_back | escalate
    options: list[str]                  # nodes the supervisor may choose from
    reasons: list[str] = field(default_factory=list)
    critic_score: float | None = None
    hard_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide(node: str, checks: list[CheckResult], critique: dict[str, Any] | None, state: dict[str, Any],
           cfg: Config) -> GateResult:
    visits: dict[str, int] = state.get("visits", {})
    fails_before = int(state.get("consecutive_failures", {}).get(node, 0))
    loopbacks = int(state.get("loopbacks", 0))
    max_iter = int(cfg.get("budgets.max_iterations_per_phase", 4))
    max_loopbacks = int(cfg.get("budgets.max_total_loopbacks", 8))
    max_retries = int(cfg.get("budgets.max_failed_retries", 2))
    threshold = float(cfg.get("gates.critic_pass_score", 6))

    hard = [c for c in checks if not c.passed and c.severity == "error"]
    warnings = [f"{c.name}: {c.detail}" for c in checks if not c.passed and c.severity == "warning"]
    score = None if critique is None else float(critique.get("score", 0))
    critic_ok = score is None or score >= threshold

    def can_visit(target: str) -> bool:
        return target == END_NODE or visits.get(target, 0) < max_iter

    def feedback(prefer: list[str] | None = None) -> list[str]:
        if loopbacks >= max_loopbacks:
            return []
        opts = [t for t in FEEDBACK[node] if can_visit(t)]
        for p in reversed(prefer or []):
            if p in opts:
                opts.remove(p)
                opts.insert(0, p)
        return opts

    base = dict(node=node, critic_score=score, hard_failures=[f"{c.name}: {c.detail}" for c in hard],
                warnings=warnings)

    def forward_options() -> list[str]:
        opts = [t for t in FORWARD[node] if can_visit(t)]
        opts += [t for t in PASS_EXTRA.get(node, []) if can_visit(t) and loopbacks < max_loopbacks]
        return opts

    if not hard and critic_ok:
        opts = forward_options()
        if not opts:
            return GateResult(decision="escalate", options=[END_NODE],
                              reasons=["all forward phases exhausted their iteration budget"], **base)
        return GateResult(decision="pass", options=opts,
                          reasons=["deterministic checks passed" + (f", critic {score:.1f}/10" if score is not None else "")],
                          **base)

    # --- failure paths ---------------------------------------------------------
    hinted = [c.route_hint for c in hard if c.route_hint in FEEDBACK[node]]
    if not hard and critique and critique.get("verdict") == "route_back" and critique.get("suggested_route") in FEEDBACK[node]:
        hinted.append(critique["suggested_route"])
    if hinted:
        opts = feedback(prefer=hinted)
        opts = [o for o in opts if o in hinted] or opts
        if opts:
            return GateResult(decision="route_back", options=opts,
                              reasons=[f"checks point upstream: {', '.join(dict.fromkeys(hinted))}"], **base)
        return GateResult(decision="escalate", options=[END_NODE],
                          reasons=["upstream fix needed but loop-back / iteration budget is exhausted"], **base)

    failures_now = fails_before + 1
    if failures_now <= max_retries and can_visit(node):
        why = "hard check failures" if hard else f"critic score {score:.1f} < {threshold}"
        return GateResult(decision="retry_same_phase", options=[node],
                          reasons=[f"{why}; retry {failures_now}/{max_retries}"], **base)

    # retries exhausted -> a different strategy is mandatory
    if not hard:
        opts = forward_options()
        if opts:
            return GateResult(decision="pass", options=opts,
                              reasons=[f"critic unconvinced after {failures_now} attempts but all deterministic "
                                       "checks pass; continuing with warnings"], **base)
    opts = feedback()
    if opts:
        return GateResult(decision="route_back", options=opts,
                          reasons=[f"{failures_now} failed attempts — switching strategy upstream"], **base)
    return GateResult(decision="escalate", options=[END_NODE],
                      reasons=[f"{failures_now} failed attempts and no loop-back budget left"], **base)

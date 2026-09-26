"""Benchmark scoring (protected). Score in [0, 1] =
  w.metric * holdout improvement over the naive baseline on the task's fixed metric
+ w.gates  * share of gate evaluations that passed (retries / loop-backs lower it)
+ w.cost   * (1 - spent / budget)
+ w.time   * (1 - wall time / limit)
A run without a holdout result gets 0 for the metric part."""
from __future__ import annotations

from typing import Any

from ada.evaluation.metrics import relative_improvement


def score_run(record: dict[str, Any], gate_events: list[dict[str, Any]], task: dict[str, Any],
              weights: dict[str, float], limits: dict[str, float]) -> dict[str, float]:
    summary = record.get("summary") or {}
    final = summary.get("final") or {}
    holdout = final.get("holdout") or {}
    metric = task["score_metric"]
    value = (holdout.get("metrics") or {}).get(metric)
    base = (holdout.get("baseline") or {}).get(metric)
    metric_score = max(0.0, min(1.0, relative_improvement(metric, value, base))) if value is not None else 0.0
    decisions = [g["payload"].get("decision") for g in gate_events if g["payload"].get("decision")]
    gate_score = (sum(d == "pass" for d in decisions) / len(decisions)) if decisions else 0.0
    if record.get("status") not in ("completed",):
        gate_score *= 0.5
    budget = summary.get("budget") or {}
    cost_score = max(0.0, 1 - float(budget.get("usd") or 0) / max(limits["max_usd"], 1e-9))
    time_score = max(0.0, 1 - float(budget.get("elapsed_seconds") or 0) / max(limits["max_wall_seconds"], 1e-9))
    total = (weights["metric"] * metric_score + weights["gates"] * gate_score + weights["cost"] * cost_score
             + weights["time"] * time_score)
    return {"total": round(total, 4), "metric": round(metric_score, 4), "gates": round(gate_score, 4),
            "cost": round(cost_score, 4), "time": round(time_score, 4),
            "holdout_value": value, "holdout_baseline": base}

"""Stub phases: a scripted fake run (no LLM, no web) that exercises the real graph,
gates and routing — used for the UI demo (`mode: stub`) and for tests.
Scenario: EDA asks for feature changes (eda -> prepare_store feedback), the first
evaluation misses the threshold (evaluation -> modeling feedback), then it passes."""
from __future__ import annotations

import time
from typing import Any

from ada.context import RunContext
from ada.gates.checks import fail, ok
from ada.graph.phases import PhaseOutcome
from ada.graph.topology import AGENTS


def _pause(ctx: RunContext) -> None:
    delay = float((ctx.cfg.get("stub", {}) or {}).get("delay", 0.0))
    if delay:
        time.sleep(delay)


def _talk(ctx: RunContext, node: str, lines: list[tuple[str, str, dict[str, Any] | None]], usd: float = 0.02) -> None:
    agent = AGENTS[node]
    for type_, text, payload in lines:
        _pause(ctx)
        ctx.emit(type_, text, node=node, agent=agent, payload=payload or {})
    ctx.budget.add_usd(usd, agent=agent)
    snap = ctx.budget.snapshot()
    ctx.emit("budget", f"{agent}: stub cost ${usd:.3f} (total ${snap['usd']:.3f})", node=node, agent=agent,
             payload={"call": {"usd": usd}, "totals": snap})


def stub_phase(ctx: RunContext, state: dict[str, Any], node: str) -> PhaseOutcome:
    visit = (state.get("visits") or {}).get(node, 1)
    agent = AGENTS[node]
    _talk(ctx, node, [("message", f"{agent} started (stub mode, visit {visit})", {"kind": "agent_start"})])
    if node == "business_objectives":
        spec = {"title": "Rental price model (demo)", "task_type": "regression", "target_variable": "monthly rent",
                "target_column_hint": "rent", "unit_of_analysis": "one listing", "primary_metric": "mae",
                "success_threshold": 350.0, "baseline": "median rent", "region": state.get("region")}
        _talk(ctx, node, [("message", "Objective → regression on monthly rent, MAE ≤ 350", None)])
        return PhaseOutcome(spec, [ok("objective_schema")], "regression, MAE ≤ 350", updates={"objective_spec": spec})
    if node == "define_data":
        _talk(ctx, node, [("tool_call", "web_search(query=\"open rental listings data\")", {"tool": "web_search"}),
                          ("tool_result", "web_search: ok — 3 candidate portals", {"tool": "web_search", "ok": True}),
                          ("message", "Need size, rooms, location, age; ≥ 500 rows", None)])
        return PhaseOutcome({"entity": "listing", "target_column": "rent", "min_rows": 500, "features": [
            {"name": "area_m2"}, {"name": "rooms"}, {"name": "lat"}, {"name": "lon"}]}, [ok("requirements_schema")],
            "4 features, ≥ 500 rows")
    if node == "collect_data":
        ctx.store.write_text("raw/stub_listings.csv", "id,rent,area_m2,rooms\n1,2100,70,3\n2,1500,45,2\n")
        _talk(ctx, node, [("tool_call", "download_file(url=\"https://example.org/listings.csv\")", {"tool": "download_file"}),
                          ("tool_result", "download_file: ok — 812 KB", {"tool": "download_file", "ok": True}),
                          ("artifact", "downloaded raw/stub_listings.csv", {"path": "raw/stub_listings.csv"})])
        return PhaseOutcome({"files": [{"path": "raw/stub_listings.csv"}], "primary_file": "raw/stub_listings.csv",
                             "license_assessment": "CC BY 4.0"}, [ok("raw_files_exist"), ok("provenance_recorded")],
                            "1 file, CC BY 4.0")
    if node == "prepare_store":
        ctx.store.write_text("data_card.md", "# Data card (demo)\n\n1 source, 2 rows.\n")
        _talk(ctx, node, [("tool_call", "run_python(purpose=\"clean + dedupe\")", {"tool": "run_python"}),
                          ("tool_result", "run_python: ok — 4,812 rows written", {"tool": "run_python", "ok": True}),
                          ("artifact", "wrote data_card.md", {"path": "data_card.md"})])
        return PhaseOutcome({"target_column": "rent", "feature_columns": ["area_m2", "rooms"], "row_count": 4812,
                             "cleaning_steps": ["dedupe"]}, [ok("clean_parquet_readable"), ok("target_present")],
                            "4,812 rows")
    if node == "eda":
        ctx.store.write_text("eda/eda_report.md", "# EDA (demo)\n\nRent scales with area.\n")
        _talk(ctx, node, [("tool_call", "run_python(purpose=\"distributions + correlations\")", {"tool": "run_python"}),
                          ("tool_result", "run_python: ok — 5 charts", {"tool": "run_python", "ok": True}),
                          ("message", "Suggest adding distance-to-centre feature", None)])
        return PhaseOutcome({"key_findings": ["rent ~ area"], "recommend_prepare_changes": visit == 1,
                             "requested_changes": ["add distance to centre"] if visit == 1 else []},
                            [ok("eda_report"), ok("charts")], "3 findings")
    if node == "modeling":
        mae = 420.0 if visit == 1 else 310.0
        for i, (name, value) in enumerate([("median baseline", 690.0), ("ridge", 520.0), ("lightgbm", mae)]):
            _pause(ctx)
            ctx.emit("metric", f"experiment {name}: mae={value}", node=node, agent=agent,
                     payload={"kind": "experiment", "index": i + 3 * (visit - 1), "name": name, "metrics": {"mae": value}})
        ctx.emit("metric", f"validation mae = {mae} (naive baseline 690)", node=node, agent="orchestrator",
                 payload={"kind": "validation", "metric": "mae", "metrics": {"mae": mae}, "baseline": {"mae": 690.0},
                          "threshold": 350.0, "visit": visit})
        validation = {"metrics": {"mae": mae}, "baseline": {"mae": 690.0}, "n_val": 800}
        _talk(ctx, node, [("message", f"Best model lightgbm, val MAE {mae}", None)])
        return PhaseOutcome({"best_model_name": "lightgbm", "features_used": ["area_m2", "rooms"], "experiments_run": 3},
                            [ok("beats_naive_baseline")], f"lightgbm val mae {mae}",
                            updates={"validation": validation, "best_model": {"name": "lightgbm", "metrics": {"mae": mae}}})
    if node == "evaluation":
        mae = (state.get("validation") or {}).get("metrics", {}).get("mae", 999)
        met = mae <= 350
        _talk(ctx, node, [("message", f"MAE {mae} {'meets' if met else 'misses'} the 350 threshold", None)])
        checks = [ok("claim_consistent"), ok("threshold_met") if met else
                  fail("threshold_met", f"mae {mae} misses threshold 350", route_hint="modeling")]
        return PhaseOutcome({"objective_met": met, "recommendation": "present" if met else "modeling",
                             "summary": "demo"}, checks, "met" if met else "not met")
    # present_results
    from ada import report
    md = f"# Demo report\n\nStub run for objective: {state.get('objective')}\n"
    ctx.store.write_text("report/final_report.md", md)
    path = report.render(ctx.store, state, ctx.budget.snapshot(), md)
    _talk(ctx, node, [("artifact", "final report ready", {"path": path})])
    return PhaseOutcome({"headline": "demo"}, [ok("report_html")], "report written",
                        updates={"final": {"holdout": {"metrics": {"mae": 318.0}, "baseline": {"mae": 701.0},
                                                       "n_holdout": 960}, "threshold_met": True}})


def stub_choice(node: str, options: list[str], state: dict[str, Any]) -> str:
    visits = state.get("visits") or {}
    if node == "prepare_store" and "eda" in options and not visits.get("eda"):
        return "eda"
    if node == "prepare_store" and "modeling" in options:
        return "modeling"
    if node == "eda" and "prepare_store" in options and (state.get("phase_outputs") or {}).get("eda", {}).get("recommend_prepare_changes"):
        return "prepare_store"
    return options[0]

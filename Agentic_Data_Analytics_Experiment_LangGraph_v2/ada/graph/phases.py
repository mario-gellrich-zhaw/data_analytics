"""Real phase implementations: build the agent's brief, run the agent, do the
orchestrator's own post-processing (holdout lock, validation harness, MLflow,
DuckDB, final holdout evaluation, report rendering) and run the deterministic
checks. Returns a PhaseOutcome; routing is done by the node wrapper."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

import pandas as pd

from ada import report, tracking
from ada.budget import BudgetExceeded
from ada.context import RunContext
from ada.evaluation import metrics as M
from ada.evaluation.harness import HarnessError, validate_model
from ada.gates.checks import CHECKS, CheckResult, fail, leakage_suspects
from agents.base import AgentFailed, RunStopped
from agents.phase_agents import PHASE_AGENTS


@dataclass
class PhaseOutcome:
    output: dict[str, Any] | None
    checks: list[CheckResult]
    summary: str
    evidence: str = ""
    updates: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    def to_cache(self) -> dict[str, Any]:
        return {"output": self.output, "checks": [c.to_dict() for c in self.checks], "summary": self.summary,
                "evidence": self.evidence, "updates": self.updates, "error": self.error}

    @classmethod
    def from_cache(cls, data: dict[str, Any]) -> "PhaseOutcome":
        return cls(output=data["output"], checks=[CheckResult(**c) for c in data["checks"]], summary=data["summary"],
                   evidence=data.get("evidence", ""), updates=data.get("updates", {}), error=data.get("error"))


# ----------------------------------------------------------------------------
def _brief(ctx: RunContext, state: dict[str, Any], node: str) -> str:
    outs = state.get("phase_outputs") or {}
    compact = {k: v for k, v in outs.items() if k != node}
    files = [f["path"] for f in ctx.store.list_files(limit=400) if not f["path"].startswith("code/")][:80]
    parts = [
        f"# Business objective\n{state['objective']}",
        f"Target market / region: {state.get('region') or 'not specified — decide based on data availability'}",
    ]
    if state.get("objective_spec") and node != "business_objectives":
        parts.append("# Objective spec\n" + json.dumps(state["objective_spec"], indent=1))
    if compact:
        parts.append("# Earlier phases (latest outputs)\n" + json.dumps(compact, indent=1, default=str)[:7000])
    if outs.get(node):
        parts.append(f"# Your previous output for this phase\n" + json.dumps(outs[node], indent=1, default=str)[:3000])
    guidance = (state.get("guidance") or {}).get(node) or []
    if guidance:
        parts.append("# Feedback you must address this time\n" + "\n".join(f"- {g}" for g in guidance[-6:]))
    parts.append("# Workspace files\n" + ("\n".join(files) if files else "(empty)"))
    if ctx.offline:
        parts.append("# Mode\nOFFLINE: no web access. Use only the local dataset catalog.")
    visit = (state.get("visits") or {}).get(node, 1)
    parts.append(f"(This is visit {visit} of this phase.)")
    return "\n\n".join(parts)


def _run_agent(ctx: RunContext, state: dict[str, Any], node: str, contract: str) -> dict[str, Any]:
    agent = PHASE_AGENTS[node](ctx, node)
    result = agent.run(_brief(ctx, state, node) + "\n\n# Your task\n" + contract)
    return result.model_dump()


def _code_evidence(ctx: RunContext, node: str, limit: int = 2) -> str:
    scripts = [f["path"] for f in ctx.store.list_files("code") if f["path"].endswith(f"_{node}.py")][-limit:]
    return "\n\n".join(f"## {s}\n```python\n{ctx.store.read_text(s, 3500)}\n```" for s in scripts)


def _file_excerpt(ctx: RunContext, rel: str, n: int = 2500) -> str:
    return f"## {rel}\n{ctx.store.read_text(rel, n)}" if ctx.store.exists(rel) else f"## {rel}\n(missing)"


def _target(state: dict[str, Any]) -> str:
    return ((state.get("phase_outputs") or {}).get("prepare_store") or {}).get("target_column") \
        or ((state.get("phase_outputs") or {}).get("define_data") or {}).get("target_column") \
        or (state.get("objective_spec") or {}).get("target_column_hint", "target")


# ----------------------------------------------------------------------------
def business_objectives(ctx: RunContext, state: dict[str, Any], node: str) -> PhaseOutcome:
    out = _run_agent(ctx, state, node, "Turn the objective into a machine-checkable ObjectiveSpec and submit it.")
    ctx.store.write_json("objective_spec.json", out)
    checks = CHECKS[node](out)
    return PhaseOutcome(out, checks, f"{out['task_type']} on '{out['target_variable']}', {out['primary_metric']} "
                                     f"≤/≥ {out['success_threshold']}", evidence=json.dumps(out),
                        updates={"objective_spec": out, "artifacts": {"objective_spec": "objective_spec.json"}})


def define_data(ctx: RunContext, state: dict[str, Any], node: str) -> PhaseOutcome:
    out = _run_agent(ctx, state, node, "Define the data requirements and submit them. Set feasible=false (with a "
                                       "reason) if the objective cannot be met with obtainable data.")
    ctx.store.write_json("data_requirements.json", out)
    return PhaseOutcome(out, CHECKS[node](out), f"{len(out['features'])} features, min {out['min_rows']} rows, "
                                               f"{len(out.get('candidate_sources', []))} candidate sources",
                        evidence=json.dumps(out)[:5000], updates={"artifacts": {"data_requirements": "data_requirements.json"}})


def collect_data(ctx: RunContext, state: dict[str, Any], node: str) -> PhaseOutcome:
    req = (state.get("phase_outputs") or {}).get("define_data") or {}
    contract = (
        "Collect the data into raw/ using download_file / import_local_dataset (these record provenance in "
        "raw/sources.json). Prefer openly licensed sources; respect robots.txt and terms of service; never bypass "
        "logins, paywalls or bot protection. Target at least "
        f"{req.get('min_rows', 'the required number of')} rows of '{req.get('entity', 'the entity')}'. "
        "Optionally enrich with open data (geocode_addresses, osm_poi_counts) once you have location fields. "
        "Inspect what you downloaded with preview_table before submitting. If the requirements cannot be met, "
        "submit obtainable=false and list what is missing."
    )
    out = _run_agent(ctx, state, node, contract)
    ctx.store.write_json("collection_report.json", out)
    sources = ctx.store.read_json("raw/sources.json", default=[]) or []
    evidence = "raw/sources.json:\n" + json.dumps(sources, indent=1)[:5000]
    attempts = _source_attempts(ctx)
    return PhaseOutcome(out, CHECKS[node](out, store=ctx.store, attempts=attempts),
                        f"{len(sources)} files with provenance ({attempts} source attempts); "
                                                                  f"primary {out.get('primary_file')}",
                        evidence=evidence, updates={"artifacts": {"sources": "raw/sources.json"}})


SOURCE_TOOLS = {"download_file", "download_zip_member", "list_remote_zip", "fetch_url", "import_local_dataset"}


def _source_attempts(ctx: RunContext) -> int:
    """Concrete source attempts in the collector's latest visit (web searches don't count)."""
    events = ctx.events.events(ctx.run_id, limit=100000)
    starts = [e["id"] for e in events if e["agent"] == "DataCollectorAgent" and e["payload"].get("kind") == "agent_start"]
    since = starts[-1] if starts else 0
    return sum(1 for e in events if e["id"] > since and e["type"] == "tool_call"
               and e["agent"] == "DataCollectorAgent" and e["payload"].get("tool") in SOURCE_TOOLS)


def prepare_store(ctx: RunContext, state: dict[str, Any], node: str) -> PhaseOutcome:
    target_hint = (state.get("phase_outputs") or {}).get("define_data", {}).get("target_column") \
        or (state.get("objective_spec") or {}).get("target_column_hint")
    locked = ctx.vault.locked
    contract = (
        "Clean, deduplicate, type-cast and enrich the raw data with run_python, then store it.\n"
        "Contract (checked by code):\n"
        "- write clean/clean.parquet: one row per entity, a column `_row_id` that is unique and STABLE across rebuilds "
        "(derive it from source identity, e.g. ada_kit.stable_row_id(source, native_id) — never from row order), "
        f"the target column `{target_hint}` (numeric for regression, no missing values), and feature columns;\n"
        "- only row-level cleaning here (parsing, units, plausibility filters, dedupe, joining enrichments by key); "
        "never impute or transform the target; leave statistics-based imputation/encoding to the modeling pipeline "
        "(missing values in features are fine);\n"
        "- drop columns that leak the target (e.g. price per m² when predicting price) or are pure identifiers/URLs;\n"
        "- write the data card to the workspace ROOT as `data_card.md` (use write_file): sources + licenses, row counts before/after each step, every column "
        "(meaning, unit, type, missing %), cleaning decisions, known issues.\n"
        + ("- A locked holdout (~20% of rows) is removed automatically from clean/ after each script; you only ever "
           "see development rows. Rebuild from raw/ as usual.\n" if locked else "")
        + "If the data is too small or too poor to meet the objective, submit needs_more_data=true with a reason."
    )
    out = _run_agent(ctx, state, node, contract)
    if ctx.vault.locked:
        ctx.vault.strip_workspace(ctx.store)
    # agents often write the card next to the data; the canonical location is the workspace root
    for alt in ("clean/data_card.md", "data/data_card.md"):
        if ctx.store.exists(alt) and not ctx.store.exists("data_card.md"):
            ctx.store.write_text("data_card.md", ctx.store.read_text(alt))
    checks = CHECKS[node](out, store=ctx.store, cfg=ctx.cfg, state=state)
    evidence = _code_evidence(ctx, node) + "\n\n" + _file_excerpt(ctx, "data_card.md", 3000)
    return PhaseOutcome(out, checks, f"{out.get('row_count')} rows, {len(out.get('feature_columns', []))} features, "
                                     f"target {out.get('target_column')}", evidence=evidence,
                        updates={"artifacts": {"clean": "clean/clean.parquet", "data_card": "data_card.md"}})


def after_prepare_pass(ctx: RunContext, state: dict[str, Any]) -> dict[str, Any]:
    """Orchestrator actions once prepare_store passes its gate: lock the holdout (first time),
    refresh splits, and load the clean table into DuckDB."""
    updates: dict[str, Any] = {}
    target = _target(state)
    if not ctx.vault.locked:
        info = ctx.vault.lock(ctx.store, target=target)
        updates["holdout_info"] = {k: v for k, v in info.items() if k != "stripped_files"}
        ctx.emit("gate", f"Locked holdout: {info.get('n_holdout', 0)} rows moved to the vault "
                         "(no agent or sandbox can read it)", node="prepare_store", agent="orchestrator",
                 payload={"kind": "holdout_locked", **updates["holdout_info"]})
    else:
        ctx.vault.strip_workspace(ctx.store)
    splits = ctx.vault.write_splits(ctx.store) or {}
    try:
        import duckdb
        db = ctx.store.resolve("clean/warehouse.duckdb")
        with duckdb.connect(str(db)) as con:
            con.execute("CREATE OR REPLACE TABLE clean AS SELECT * FROM read_parquet(?)",
                        [str(ctx.store.resolve("clean/clean.parquet"))])
        db.chmod(0o666)
        ctx.emit("artifact", "stored clean table in clean/warehouse.duckdb", node="prepare_store",
                 agent="orchestrator", payload={"path": "clean/warehouse.duckdb"})
    except Exception as exc:
        ctx.emit("error", f"DuckDB store failed: {exc}", node="prepare_store", agent="orchestrator")
    updates["artifacts"] = {"splits": "data/splits.json", "warehouse": "clean/warehouse.duckdb"}
    ctx.emit("message", f"Fixed split: {splits.get('n_train')} train / {splits.get('n_val')} validation rows",
             node="prepare_store", agent="orchestrator", payload={"kind": "splits", **{k: splits.get(k) for k in ("n_train", "n_val")}})
    return updates


def eda(ctx: RunContext, state: dict[str, Any], node: str) -> PhaseOutcome:
    target = _target(state)
    suspects = {}
    if ctx.store.exists("clean/clean.parquet"):
        df = pd.read_parquet(ctx.store.resolve("clean/clean.parquet"))
        suspects = leakage_suspects(df, target, float(ctx.cfg.get("gates.leakage_corr_threshold", 0.97)))
    contract = (
        "Explore clean/clean.parquet with run_python (use ada_kit.load_clean()). Cover: target distribution, "
        "feature distributions and missingness, correlations with the target, geographic patterns if coordinates or "
        "regions exist, segment differences, leakage suspects, and concrete feature-engineering ideas. Save 4–8 "
        "clear PNG charts with ada_kit.save_chart(fig, name). Write eda/eda_report.md (with write_file) that embeds "
        "the charts as ![caption](charts/<name>.png) and states findings with numbers.\n"
        f"Deterministic leakage scan flagged: {json.dumps(suspects) if suspects else 'nothing'}.\n"
        "Set recommend_prepare_changes=true only if data preparation must change before modeling."
    )
    out = _run_agent(ctx, state, node, contract)
    checks = CHECKS[node](out, store=ctx.store)
    evidence = _code_evidence(ctx, node) + "\n\n" + _file_excerpt(ctx, "eda/eda_report.md", 3500)
    return PhaseOutcome(out, checks, f"{len(out.get('key_findings', []))} findings, {len(out.get('charts', []))} charts",
                        evidence=evidence, updates={"artifacts": {"eda_report": "eda/eda_report.md"}})


def modeling(ctx: RunContext, state: dict[str, Any], node: str) -> PhaseOutcome:
    spec = state.get("objective_spec") or {}
    target = _target(state)
    ctx.vault.write_splits(ctx.store)  # restore the orchestrator's split (tamper-proof, hash based)
    df_cols: list[str] = []
    suspects: dict[str, str] = {}
    if ctx.store.exists("clean/clean.parquet"):
        df = pd.read_parquet(ctx.store.resolve("clean/clean.parquet"))
        df_cols = list(df.columns)
        suspects = leakage_suspects(df, target, float(ctx.cfg.get("gates.leakage_corr_threshold", 0.97)))
    prior = state.get("validation")
    contract = (
        f"Train models for target `{target}` ({spec.get('task_type')}); primary metric {spec.get('primary_metric')} "
        f"(success threshold {spec.get('success_threshold')}).\n"
        "Rules (checked by code):\n"
        "- use train, val = ada_kit.train_val() — the fixed split; the locked test set is not available to you;\n"
        "- fit a naive baseline (median/mean or majority) and a linear model first, then stronger models (e.g. "
        "LightGBM / HistGradientBoosting) with cross-validation on TRAIN and modest tuning; keep each script under "
        "~5 minutes;\n"
        "- log EVERY fitted model with ada_kit.log_experiment(name, model_type, params, metrics_on_val, features);\n"
        "- the final model must be ONE sklearn-compatible object (use a Pipeline with a ColumnTransformer for "
        "imputation/encoding) whose .predict(DataFrame) works on clean-data columns; save it with "
        "ada_kit.save_model(model, features, target, train_row_ids=train['_row_id'], name=..., metrics=...);\n"
        f"- never use these leakage suspects as features: {list(suspects) or 'none flagged'}; never use `_row_id` or the target.\n"
        f"Columns available: {df_cols[:80]}\n"
        + (f"Previous best validation result: {json.dumps(prior['metrics'])} vs baseline {json.dumps(prior['baseline'])}\n"
           if prior else "")
        + "If the features are insufficient, set needs_features=true and list requested_changes."
    )
    out = _run_agent(ctx, state, node, contract)
    tracking.log_new_experiments(ctx, node)
    validation, harness_error = None, None
    try:
        validation = validate_model(ctx.store, ctx.sandbox, task_type=spec.get("task_type", "regression"), target=target)
        metric = spec.get("primary_metric", "mae")
        ctx.emit("metric", f"validation {metric} = {validation['metrics'].get(metric):.4g} "
                           f"(naive baseline {validation['baseline'].get(metric):.4g})", node=node, agent="orchestrator",
                 payload={"kind": "validation", "metric": metric, "metrics": validation["metrics"],
                          "baseline": validation["baseline"], "threshold": spec.get("success_threshold"),
                          "visit": (state.get("visits") or {}).get(node)})
        tracking.log_best_model(ctx, validation, node)
    except HarnessError as exc:
        harness_error = str(exc)
        ctx.emit("error", f"validation harness: {exc}", node=node, agent="orchestrator")
    checks = CHECKS[node](out, store=ctx.store, cfg=ctx.cfg, state=state, validation=validation,
                          holdout_ids=ctx.vault.holdout_ids())
    if harness_error:
        checks.append(fail("harness_error", harness_error[:600]))
    exps = ctx.store.read_text("experiments.jsonl")[-3000:] if ctx.store.exists("experiments.jsonl") else ""
    evidence = _code_evidence(ctx, node) + f"\n\n## experiments.jsonl (tail)\n{exps}\n\n## harness validation\n" + \
        json.dumps(validation, default=str)
    updates: dict[str, Any] = {"artifacts": {"model": "models/best_model.pkl", "experiments": "experiments.jsonl"}}
    if validation:
        updates["validation"] = validation
        updates["best_model"] = {"path": "models/best_model.pkl", "name": out.get("best_model_name"),
                                 "metrics": validation["metrics"]}
    metric = spec.get("primary_metric", "mae")
    summary = f"{out.get('best_model_name')}: val {metric} " + (f"{validation['metrics'].get(metric):.4g}" if validation else "n/a")
    return PhaseOutcome(out, checks, summary, evidence=evidence, updates=updates)


def evaluation(ctx: RunContext, state: dict[str, Any], node: str) -> PhaseOutcome:
    spec = state.get("objective_spec") or {}
    validation = state.get("validation")
    if not validation or not ctx.store.exists("models/best_model.pkl"):
        # nothing to evaluate yet (e.g. arrived from EDA): route to modeling without spending tokens
        checks = CHECKS[node](None, state=state, validation=None)
        return PhaseOutcome(None, checks, "no validated model yet", error=None)
    target = _target(state)
    metric = spec.get("primary_metric")
    met = M.meets_threshold(metric, validation["metrics"].get(metric), spec.get("success_threshold"))
    contract = (
        f"Evaluate the current best model (models/best_model.pkl, meta in models/best_model_meta.json) for target "
        f"`{target}`.\nOrchestrator's validation result: {json.dumps(validation['metrics'])}; naive baseline "
        f"{json.dumps(validation['baseline'])}; threshold {metric} {spec.get('success_threshold')} is "
        f"{'MET' if met else 'NOT met'}.\n"
        "With run_python (validation rows only via ada_kit.train_val()): error analysis by meaningful segments, "
        "explainability (SHAP or permutation importance) with a chart saved to evaluation/, robustness checks "
        "(e.g. stability across segments / sensitivity to missing features), residual plot. Write "
        "evaluation/evaluation.md. Then decide: recommendation=present if the objective is met (or if further "
        "iterations are unlikely to help — explain honestly why), modeling if better models/tuning should help, "
        "eda if the data/features need investigation. objective_met must agree with the numbers."
    )
    out = _run_agent(ctx, state, node, contract)
    record = {**out, "validation": validation, "threshold": spec.get("success_threshold"), "metric": metric,
              "threshold_met_by_code": met}
    ctx.store.write_json("evaluation.json", record)
    checks = CHECKS[node](out, state=state, validation=validation)
    evidence = _code_evidence(ctx, node) + "\n\n" + _file_excerpt(ctx, "evaluation/evaluation.md", 3000)
    return PhaseOutcome(out, checks, f"objective {'met' if met else 'not met'}; recommends {out['recommendation']}",
                        evidence=evidence, updates={"artifacts": {"evaluation": "evaluation.json"}})


def present_results(ctx: RunContext, state: dict[str, Any], node: str) -> PhaseOutcome:
    spec = state.get("objective_spec") or {}
    final: dict[str, Any] = {}
    if ctx.store.exists("models/best_model.pkl") and ctx.vault.locked and not ctx.vault.evaluated:
        try:
            hold = ctx.vault.final_evaluate(ctx.store, ctx.sandbox, task_type=spec.get("task_type", "regression"),
                                            target=_target(state))
            metric = spec.get("primary_metric", "mae")
            final = {"holdout": hold, "threshold_met": M.meets_threshold(metric, hold["metrics"].get(metric),
                                                                         spec.get("success_threshold"))}
            ctx.emit("metric", f"FINAL locked-holdout {metric} = {hold['metrics'].get(metric):.4g} "
                               f"(baseline {hold['baseline'].get(metric):.4g}, n={hold['n_holdout']})",
                     node=node, agent="orchestrator", payload={"kind": "holdout", "metric": metric, **hold,
                                                               "threshold": spec.get("success_threshold")})
        except Exception as exc:
            final = {"holdout_error": str(exc)[:500]}
            ctx.emit("error", f"final holdout evaluation failed: {exc}", node=node, agent="orchestrator")
    elif not ctx.store.exists("models/best_model.pkl"):
        final = {"holdout_error": "no model was produced"}
    state = {**state, "final": final}
    ctx.store.write_json("evaluation/final_holdout.json", final)
    facts = _run_facts(ctx, state)
    contract = (
        "Write the final report as Markdown to report/final_report.md with write_file. Audience: business "
        "stakeholders plus a technical appendix. Sections: Objective; Data sources and licenses; Method (cleaning, "
        "features, models tried); Results (validation and the locked-holdout numbers below, vs. baseline and "
        "threshold); Key insights (embed 2–4 charts from eda/charts or evaluation/ as ![caption](path)); "
        "Limitations and risks; Model card (intended use, training data, metrics, caveats, ethical considerations). "
        "Be honest: if the threshold was not met, say so and explain why.\n"
        "STRICT: describe only work that the recorded facts below show. Never describe models, experiments or "
        "analyses for phases that did not run, and quote numbers only from these facts or from files you read.\n"
        f"# Recorded facts (from the orchestrator)\n{json.dumps(facts, indent=1, default=str)[:6000]}\n"
        + (f"NOTE: the run stopped early — {state.get('stop_reason')}. Present the best result so far and say "
           "clearly what was not done.\n" if state.get("stop_reason") else "")
    )
    headline, md = "", None
    try:
        if ctx.budget.exhausted_reason(use_reserve=True):
            raise BudgetExceeded("no budget left for the presenter")
        brief = _brief(ctx, state, node) + "\n\n# Your task\n" + contract
        for attempt in range(2):
            agent = PHASE_AGENTS[node](ctx, node)
            agent.use_reserve = True
            out = agent.run(brief).model_dump()
            headline = out.get("headline", "")
            md = ctx.store.read_text("report/final_report.md") if ctx.store.exists("report/final_report.md") else None
            if not md or attempt == 1 or ctx.budget.exhausted_reason(use_reserve=True):
                break
            critique = _review_report(ctx, node, md, facts)
            if critique is None or critique.get("verdict") == "accept":
                break
            brief += ("\n\n# Fact-check feedback — revise the report\n" + critique.get("feedback_for_agent", "") +
                      "\nIssues: " + "; ".join(critique.get("issues", [])))
    except (BudgetExceeded, AgentFailed, RunStopped) as exc:
        ctx.emit("message", f"Presenter fallback (template report): {exc}", node=node, agent="orchestrator")
    if not md:
        md = report.fallback_markdown(ctx.store, state)
        ctx.store.write_text("report/final_report.md", md)
    path = report.render(ctx.store, state, ctx.budget.snapshot(), md)
    ctx.emit("artifact", "final report ready", node=node, agent="PresenterAgent", payload={"path": path})
    return PhaseOutcome({"headline": headline, "report": path}, CHECKS[node](None, store=ctx.store), headline or "report written",
                        updates={"final": final, "artifacts": {"final_report": path}})


def _run_facts(ctx: RunContext, state: dict[str, Any]) -> dict[str, Any]:
    visits = state.get("visits") or {}
    ran = [n for n in PHASE_AGENTS if visits.get(n) and n != "present_results"]
    exps = []
    if ctx.store.exists("experiments.jsonl"):
        for line in ctx.store.read_text("experiments.jsonl").splitlines()[-25:]:
            try:
                rec = json.loads(line)
                exps.append({"name": rec.get("name"), "model_type": rec.get("model_type"), "metrics": rec.get("metrics")})
            except json.JSONDecodeError:
                pass
    return {
        "phases_that_ran": ran,
        "phases_that_never_ran": [n for n in PHASE_AGENTS if n not in ran and n != "present_results"],
        "gate_history": [{k: h.get(k) for k in ("node", "visit", "decision", "next", "summary")}
                         for h in state.get("phase_history") or []],
        "experiments_logged": exps,
        "validation_of_best_model": state.get("validation"),
        "locked_holdout": state.get("final"),
        "stop_reason": state.get("stop_reason"),
        "clean_rows": _count_rows(ctx, "clean/clean.parquet"),
    }


def _count_rows(ctx: RunContext, rel: str) -> int | None:
    if not ctx.store.exists(rel):
        return None
    try:
        return int(len(pd.read_parquet(ctx.store.resolve(rel), columns=["_row_id"])))
    except Exception:
        return None


def _review_report(ctx: RunContext, node: str, md: str, facts: dict[str, Any]) -> dict[str, Any] | None:
    from agents.control import CriticAgent
    try:
        critique = CriticAgent(ctx, node).review(
            phase="present_results (fact-check the final report against the recorded facts)",
            output={"report_markdown": md[:9000]}, checks=[], evidence=json.dumps(facts, default=str)[:9000],
            allowed_routes=[])
    except (AgentFailed, BudgetExceeded):
        return None
    data = critique.model_dump()
    ctx.emit("message", f"Critic {data['score']:.1f}/10 on the report ({data['verdict']}): " +
             ("; ".join(data["issues"][:3]) or "no issues"), node=node, agent="CriticAgent",
             payload={"kind": "critique", **data})
    return data


PHASES: dict[str, Callable[[RunContext, dict[str, Any], str], PhaseOutcome]] = {
    "business_objectives": business_objectives,
    "define_data": define_data,
    "collect_data": collect_data,
    "prepare_store": prepare_store,
    "eda": eda,
    "modeling": modeling,
    "evaluation": evaluation,
    "present_results": present_results,
}

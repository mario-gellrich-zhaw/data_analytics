"""MLflow logging. Agent code in the sandbox only appends to experiments.jsonl
(no MLflow/network access there); the orchestrator mirrors new lines into MLflow."""
from __future__ import annotations

import json
import os
from typing import Any

from ada.paths import var_dir


def tracking_uri() -> str:
    return os.environ.get("MLFLOW_TRACKING_URI") or f"sqlite:///{var_dir() / 'mlflow.db'}"


def _flat(params: dict[str, Any]) -> dict[str, str]:
    out = {}
    for k, v in (params or {}).items():
        out[str(k)[:250]] = json.dumps(v, default=str)[:500] if not isinstance(v, (str, int, float, bool)) else str(v)[:500]
    return out


def log_new_experiments(ctx: Any, node: str) -> list[dict[str, Any]]:
    store = ctx.store
    if not store.exists("experiments.jsonl"):
        return []
    lines = [l for l in store.read_text("experiments.jsonl").splitlines() if l.strip()]
    done = int((store.read_json(".cache/mlflow_logged.json", default={}) or {}).get("n", 0))
    new = []
    for i, line in enumerate(lines[done:], start=done):
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        rec["index"] = i
        new.append(rec)
    if not new:
        return []
    try:
        import mlflow
        mlflow.set_tracking_uri(tracking_uri())
        name = f"ada-{ctx.run_id}"
        if mlflow.get_experiment_by_name(name) is None:
            mlflow.create_experiment(name, artifact_location=str(var_dir() / "mlartifacts" / ctx.run_id))
        mlflow.set_experiment(name)
        for rec in new:
            with mlflow.start_run(run_name=str(rec.get("name", "experiment"))[:200]):
                mlflow.log_params(_flat(rec.get("params", {})))
                metrics = {k: float(v) for k, v in (rec.get("metrics") or {}).items()
                           if isinstance(v, (int, float)) and v == v}
                if metrics:
                    mlflow.log_metrics(metrics)
                mlflow.set_tags({"model_type": str(rec.get("model_type", "")), "phase": node, "ada_run": ctx.run_id,
                                 "n_features": str(len(rec.get("features") or [])), "cv": str(rec.get("cv", False))})
    except Exception as exc:  # tracking must never break a run
        ctx.emit("error", f"MLflow logging failed: {exc}", node=node, agent="orchestrator")
    for rec in new:
        ctx.emit("metric", f"experiment {rec.get('name')}: " +
                 ", ".join(f"{k}={v:.4g}" for k, v in (rec.get("metrics") or {}).items() if isinstance(v, (int, float))),
                 node=node, agent="ModelingAgent",
                 payload={"kind": "experiment", "index": rec["index"], "name": rec.get("name"),
                          "model_type": rec.get("model_type"), "metrics": rec.get("metrics", {})})
    store.write_json(".cache/mlflow_logged.json", {"n": len(lines)})
    return new


def log_best_model(ctx: Any, validation: dict[str, Any], node: str) -> None:
    try:
        import mlflow
        mlflow.set_tracking_uri(tracking_uri())
        mlflow.set_experiment(f"ada-{ctx.run_id}")
        meta = ctx.store.read_json("models/best_model_meta.json", default={}) or {}
        with mlflow.start_run(run_name=f"best:{meta.get('name', 'model')}"):
            mlflow.log_metrics({f"val_{k}": float(v) for k, v in validation["metrics"].items() if v == v})
            mlflow.log_metrics({f"baseline_{k}": float(v) for k, v in validation["baseline"].items() if v == v})
            mlflow.set_tags({"ada_run": ctx.run_id, "role": "best_model", "phase": node})
            mlflow.log_artifact(str(ctx.store.resolve("models/best_model.pkl")))
            mlflow.log_artifact(str(ctx.store.resolve("models/best_model_meta.json")))
    except Exception as exc:
        ctx.emit("error", f"MLflow best-model logging failed: {exc}", node=node, agent="orchestrator")

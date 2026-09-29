"""Orchestrator-side model scoring (protected).

`validate_model` scores models/best_model.pkl on the fixed validation split.
The model is agent-produced code (a pickle), so it is only ever unpickled inside
the sandbox by `harness_run.py`; targets stay outside.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from ada.evaluation import metrics as M
from ada.sandbox.executor import Sandbox
from ada.store import RunStore

HARNESS_SCRIPT = Path(__file__).resolve().parent / "harness_run.py"


class HarnessError(RuntimeError):
    pass


def predict_in_sandbox(store: RunStore, sandbox: Sandbox, *, data_rel: str, target: str,
                       out_rel: str, ids: str = "") -> dict[str, Any]:
    args = ["--model", "models/best_model.pkl", "--meta", "models/best_model_meta.json",
            "--data", data_rel, "--target", target, "--out", out_rel]
    if ids:
        args += ["--ids", ids]
    store.ensure_dir(str(Path(out_rel).parent))
    res = sandbox.run_script(store.root, HARNESS_SCRIPT, args=args, timeout=600)
    if not res.ok:
        raise HarnessError(f"prediction harness failed (rc={res.returncode}): {res.stderr[-1500:]}")
    out = store.read_json(out_rel)
    if not out:
        raise HarnessError("prediction harness wrote no output")
    return out


def validate_model(store: RunStore, sandbox: Sandbox, *, task_type: str, target: str) -> dict[str, Any]:
    if not store.exists("models/best_model.pkl"):
        raise HarnessError("models/best_model.pkl does not exist")
    splits = store.read_json("data/splits.json")
    if not splits:
        raise HarnessError("data/splits.json missing (holdout not locked yet?)")
    clean = pd.read_parquet(store.resolve("clean/clean.parquet"))
    clean["_row_id"] = clean["_row_id"].astype(str)
    out = predict_in_sandbox(store, sandbox, data_rel="clean/clean.parquet", target=target,
                             out_rel="evaluation/val_predictions.json", ids="data/splits.json:val_ids")
    pred = pd.DataFrame({"_row_id": out["row_ids"], "pred": out["pred"]})
    if "score" in out:
        pred["score"] = out["score"]
    merged = pred.merge(clean[["_row_id", target]], on="_row_id", how="inner")
    if merged.empty:
        raise HarnessError("no validation rows could be scored")
    y_train = clean.loc[clean["_row_id"].isin(set(map(str, splits["train_ids"]))), target]
    result = {
        "n_val": int(len(merged)),
        "metrics": M.compute(task_type, merged[target], merged["pred"], merged.get("score")),
        "baseline": M.naive_baseline(task_type, y_train, merged[target]),
        "input_layout": out.get("input"),
    }
    store.write_json("evaluation/val_metrics.json", result)
    return result


def score_holdout(store: RunStore, sandbox: Sandbox, holdout: pd.DataFrame, *, task_type: str,
                  target: str) -> dict[str, Any]:
    """Called exactly once by the vault. `holdout` includes the target; only X enters the sandbox."""
    tmp = ".final_eval"
    store.ensure_dir(tmp)
    try:
        X = holdout.drop(columns=[target])
        x_path = store.resolve(f"{tmp}/X.parquet")
        X.to_parquet(x_path, index=False)
        x_path.chmod(0o666)
        out = predict_in_sandbox(store, sandbox, data_rel=f"{tmp}/X.parquet", target=target,
                                 out_rel=f"{tmp}/pred.json")
    finally:
        for f in store.resolve(tmp).glob("*"):
            f.unlink(missing_ok=True)
    pred = pd.DataFrame({"_row_id": out["row_ids"], "pred": out["pred"]})
    if "score" in out:
        pred["score"] = out["score"]
    hold = holdout.copy()
    hold["_row_id"] = hold["_row_id"].astype(str)
    merged = pred.merge(hold[["_row_id", target]], on="_row_id", how="inner")
    clean = pd.read_parquet(store.resolve("clean/clean.parquet"))
    return {
        "n_holdout": int(len(merged)),
        "metrics": M.compute(task_type, merged[target], merged["pred"], merged.get("score")),
        "baseline": M.naive_baseline(task_type, clean[target], merged[target]),
    }


def json_safe(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=float))

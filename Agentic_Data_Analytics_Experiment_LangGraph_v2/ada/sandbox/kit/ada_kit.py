"""Helper module available to agent code inside the sandbox: `import ada_kit`.

Workspace layout (cwd = workspace):
  raw/                      downloaded / imported source files (+ sources.json)
  clean/clean.parquet       cleaned, enriched table (must contain `_row_id` and the target)
  data/splits.json          train/validation row ids (written by the orchestrator)
  eda/charts/*.png          charts
  models/best_model.pkl     final model (sklearn-compatible, .predict(DataFrame))
  experiments.jsonl         one line per experiment (logged to MLflow by the orchestrator)

The locked holdout test set is NOT in the workspace and cannot be read from here.
"""
from __future__ import annotations

import json
import os
import pickle
import time
from pathlib import Path
from typing import Any, Iterable

WORKSPACE = Path(os.environ.get("ADA_WORKSPACE", os.getcwd()))
CLEAN_PATH = WORKSPACE / "clean" / "clean.parquet"
SPLITS_PATH = WORKSPACE / "data" / "splits.json"
MODEL_PATH = WORKSPACE / "models" / "best_model.pkl"
MODEL_META_PATH = WORKSPACE / "models" / "best_model_meta.json"
EXPERIMENTS_PATH = WORKSPACE / "experiments.jsonl"
CHARTS_DIR = WORKSPACE / "eda" / "charts"
ROW_ID = "_row_id"


def path(*parts: str) -> Path:
    return WORKSPACE.joinpath(*parts)


def load_clean():
    import pandas as pd
    return pd.read_parquet(CLEAN_PATH)


def load_splits() -> dict[str, Any]:
    return json.loads(SPLITS_PATH.read_text())


def train_val(df=None):
    """Return (train_df, val_df) using the orchestrator's fixed split."""
    df = load_clean() if df is None else df
    splits = load_splits()
    train_ids, val_ids = set(splits["train_ids"]), set(splits["val_ids"])
    ids = df[ROW_ID].astype(str)
    return df[ids.isin(train_ids)].copy(), df[ids.isin(val_ids)].copy()


def stable_row_id(*values: Any) -> str:
    """Deterministic id from source identity fields (e.g. source name + listing id)."""
    import hashlib
    raw = "|".join("" if v is None else str(v) for v in values)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def regression_metrics(y_true, y_pred) -> dict[str, float]:
    import numpy as np
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    err = y_pred - y_true
    nonzero = np.abs(y_true) > 1e-9
    ss_tot = float(((y_true - y_true.mean()) ** 2).sum()) or 1e-12
    return {
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "mape": float((np.abs(err[nonzero]) / np.abs(y_true[nonzero])).mean()) if nonzero.any() else float("nan"),
        "r2": float(1 - (err ** 2).sum() / ss_tot),
    }


def classification_metrics(y_true, y_pred, y_score=None) -> dict[str, float]:
    from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
    out = {"accuracy": float(accuracy_score(y_true, y_pred)),
           "f1_macro": float(f1_score(y_true, y_pred, average="macro"))}
    if y_score is not None:
        try:
            out["roc_auc"] = float(roc_auc_score(y_true, y_score))
        except ValueError:
            pass
    return out


def log_experiment(name: str, model_type: str, params: dict[str, Any], metrics: dict[str, float],
                   features: Iterable[str] = (), notes: str = "", cv: bool = False) -> None:
    """Record one experiment (validation metrics). Every model you fit should be logged."""
    EXPERIMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": time.time(), "name": name, "model_type": model_type, "params": params,
              "metrics": {k: float(v) for k, v in metrics.items()}, "features": list(features),
              "notes": notes, "cv": cv}
    with open(EXPERIMENTS_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")


def save_model(model: Any, features: list[str], target: str, train_row_ids: Iterable[Any],
               name: str, metrics: dict[str, float] | None = None, notes: str = "") -> Path:
    """Save the chosen model. It must accept a DataFrame with (at least) `features` via .predict()."""
    try:
        import cloudpickle as serializer  # handles functions/classes defined in the script
    except ImportError:  # pragma: no cover
        serializer = pickle
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MODEL_PATH, "wb") as fh:
        serializer.dump(model, fh)
    meta = {"name": name, "features": list(features), "target": target,
            "train_row_ids": [str(i) for i in train_row_ids], "metrics": metrics or {},
            "notes": notes, "saved_at": time.time()}
    MODEL_META_PATH.write_text(json.dumps(meta, default=str))
    return MODEL_PATH


def save_chart(fig, name: str) -> Path:
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    out = CHARTS_DIR / (name if name.endswith(".png") else f"{name}.png")
    fig.savefig(out, dpi=110, bbox_inches="tight")
    return out

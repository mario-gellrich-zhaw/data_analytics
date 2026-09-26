"""Metrics and naive baselines, computed by the orchestrator (never by agents).
Protected: the improver may not edit anything under ada/evaluation/."""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

MINIMIZE = {"mae", "rmse", "mape"}
MAXIMIZE = {"r2", "accuracy", "f1_macro", "roc_auc"}
REGRESSION_METRICS = ["mae", "rmse", "mape", "r2"]
CLASSIFICATION_METRICS = ["accuracy", "f1_macro", "roc_auc"]


def direction(metric: str) -> str:
    return "minimize" if metric in MINIMIZE else "maximize"


def regression_metrics(y_true: Any, y_pred: Any) -> dict[str, float]:
    y_true = np.asarray(y_true, dtype=float)
    y_pred = np.asarray(y_pred, dtype=float)
    err = y_pred - y_true
    nonzero = np.abs(y_true) > 1e-9
    ss_tot = float(((y_true - y_true.mean()) ** 2).sum()) or 1e-12
    return {
        "mae": float(np.abs(err).mean()),
        "rmse": float(np.sqrt((err ** 2).mean())),
        "mape": float((np.abs(err[nonzero]) / np.abs(y_true[nonzero])).mean()) if nonzero.any() else math.nan,
        "r2": float(1 - (err ** 2).sum() / ss_tot),
    }


def classification_metrics(y_true: Any, y_pred: Any, y_score: Any = None) -> dict[str, float]:
    from sklearn.metrics import accuracy_score, f1_score, roc_auc_score
    y_true = pd.Series(y_true).astype(str).to_numpy()
    y_pred = pd.Series(y_pred).astype(str).to_numpy()
    out = {"accuracy": float(accuracy_score(y_true, y_pred)),
           "f1_macro": float(f1_score(y_true, y_pred, average="macro"))}
    if y_score is not None and len(set(y_true)) == 2:
        try:
            positive = sorted(set(y_true))[1]
            out["roc_auc"] = float(roc_auc_score((y_true == positive).astype(int), np.asarray(y_score, dtype=float)))
        except ValueError:
            pass
    return out


def compute(task_type: str, y_true: Any, y_pred: Any, y_score: Any = None) -> dict[str, float]:
    if task_type == "classification":
        return classification_metrics(y_true, y_pred, y_score)
    return regression_metrics(y_true, y_pred)


def naive_baseline(task_type: str, y_train: Any, y_eval: Any) -> dict[str, float]:
    """Regression: predict the training median. Classification: predict the majority class."""
    y_train = pd.Series(y_train).dropna()
    n = len(y_eval)
    if task_type == "classification":
        majority = y_train.astype(str).mode().iloc[0]
        metrics = classification_metrics(y_eval, [majority] * n)
        metrics["roc_auc"] = 0.5
        return metrics
    return regression_metrics(y_eval, np.full(n, float(y_train.median())))


def relative_improvement(metric: str, value: float, baseline: float) -> float:
    """> 0 means better than the baseline; scaled so 1.0 is 'perfect'."""
    if value is None or baseline is None or math.isnan(value) or math.isnan(baseline):
        return 0.0
    if direction(metric) == "minimize":
        return (baseline - value) / baseline if baseline > 0 else 0.0
    ceiling = 1.0
    return (value - baseline) / (ceiling - baseline) if ceiling > baseline else 0.0


def meets_threshold(metric: str, value: float, threshold: float | None) -> bool:
    if threshold is None or value is None or math.isnan(value):
        return False
    return value <= threshold if direction(metric) == "minimize" else value >= threshold

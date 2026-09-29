"""Prediction harness — executed INSIDE the sandbox by the orchestrator (never by agents).

It only produces predictions; metrics are computed outside the sandbox, so the
model code never sees the targets of the rows it is scored on.

    python harness_run.py --model models/best_model.pkl --data clean/clean.parquet \
        --ids data/splits.json:val_ids --target rent --out evaluation/val_predictions.json
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import traceback

import pandas as pd


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--meta", default="")
    ap.add_argument("--data", required=True)
    ap.add_argument("--ids", default="")
    ap.add_argument("--target", default="")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    df = pd.read_parquet(a.data)
    if a.ids:
        file, key = a.ids.split(":", 1)
        ids = set(map(str, json.load(open(file))[key]))
        df = df[df["_row_id"].astype(str).isin(ids)]
    X = df.drop(columns=[a.target], errors="ignore")
    features = []
    if a.meta:
        try:
            features = json.load(open(a.meta)).get("features", [])
        except Exception:
            features = []
    with open(a.model, "rb") as fh:
        model = pickle.load(fh)

    attempts = []
    if features and all(f in X.columns for f in features):
        attempts.append(("features", X[features]))
    attempts.append(("all_columns", X))
    attempts.append(("all_but_row_id", X.drop(columns=["_row_id"], errors="ignore")))
    pred, used, errors = None, None, []
    for label, frame in attempts:
        try:
            pred = model.predict(frame)
            used = (label, frame)
            break
        except Exception as exc:  # try the next input layout
            errors.append(f"{label}: {exc!r}")
    if pred is None:
        print("prediction failed:\n" + "\n".join(errors), file=sys.stderr)
        return 2

    out = {"row_ids": X["_row_id"].astype(str).tolist(), "pred": pd.Series(pred).tolist(), "input": used[0]}
    if hasattr(model, "predict_proba"):
        try:
            proba = model.predict_proba(used[1])
            if proba.shape[1] == 2:
                out["score"] = proba[:, 1].tolist()
        except Exception:
            pass
    with open(a.out, "w") as fh:
        json.dump(out, fh, default=float)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(3)

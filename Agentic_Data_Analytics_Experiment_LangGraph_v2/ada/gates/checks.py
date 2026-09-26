"""Deterministic (non-LLM) checks run before every gate. Protected code."""
from __future__ import annotations

import json
import math
import re
from dataclasses import asdict, dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd

from ada.config import Config
from ada.evaluation import metrics as M
from ada.store import RunStore, sha256_file

TABULAR = {".csv", ".tsv", ".parquet", ".json", ".xlsx", ".xls", ".geojson", ".txt", ".zip", ".gz", ".xml"}
POST_HOC_WORDS = ("sold", "final_price", "closing", "outcome", "after_", "post_", "result", "actual_")
ID_WORDS = ("id", "url", "link", "uuid", "key", "index", "listing_number", "ref")


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str = ""
    severity: str = "error"            # error | warning
    route_hint: str | None = None      # phase to route back to when this fails

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def ok(name: str, detail: str = "") -> CheckResult:
    return CheckResult(name, True, detail)


def fail(name: str, detail: str, *, severity: str = "error", route_hint: str | None = None) -> CheckResult:
    return CheckResult(name, False, detail, severity, route_hint)


def _schema(name: str, output: dict | None, model: type) -> CheckResult:
    if not output:
        return fail(name, "agent produced no output")
    try:
        model.model_validate(output)
        return ok(name, "output matches schema")
    except Exception as exc:  # pydantic.ValidationError
        return fail(name, f"schema validation failed: {exc}"[:500])


# ---------------------------------------------------------------------------
def check_business_objectives(output: dict | None, **_: Any) -> list[CheckResult]:
    from ada.schemas import ObjectiveSpec
    res = [_schema("objective_schema", output, ObjectiveSpec)]
    if not res[0].passed:
        return res
    spec = output or {}
    task, metric = spec["task_type"], spec["primary_metric"]
    reg, clf = set(M.REGRESSION_METRICS), set(M.CLASSIFICATION_METRICS)
    if task == "regression" and metric not in reg or task == "classification" and metric not in clf:
        res.append(fail("metric_matches_task", f"{metric} is not a {task} metric"))
    else:
        res.append(ok("metric_matches_task"))
    thr = spec.get("success_threshold")
    sane = isinstance(thr, (int, float)) and math.isfinite(thr) and thr >= 0
    if metric in ("mape", "r2", "accuracy", "f1_macro", "roc_auc"):
        sane = sane and thr <= 1.0
    res.append(ok("threshold_sane", str(thr)) if sane else
               fail("threshold_sane", f"success_threshold {thr!r} is not sensible for {metric} "
                                      "(ratios such as mape/r2/accuracy must be fractions in [0, 1])"))
    if not re.fullmatch(r"[a-z][a-z0-9_]*", spec.get("target_column_hint", "")):
        res.append(fail("target_column_snake_case", "target_column_hint must be snake_case", severity="warning"))
    return res


def check_define_data(output: dict | None, **_: Any) -> list[CheckResult]:
    from ada.schemas import DataRequirements
    res = [_schema("requirements_schema", output, DataRequirements)]
    if not res[0].passed:
        return res
    req = output or {}
    if not req.get("feasible", True):
        res.append(fail("objective_feasible", req.get("infeasibility_reason") or "declared infeasible",
                        route_hint="business_objectives"))
    feats = req.get("features") or []
    res.append(ok("features_defined", f"{len(feats)} features") if len(feats) >= 2
               else fail("features_defined", "fewer than 2 features specified"))
    res.append(ok("min_rows_defined", str(req.get("min_rows"))) if req.get("min_rows", 0) >= 50
               else fail("min_rows_defined", "min_rows < 50 is too small to fit and validate a model",
                         severity="warning"))
    return res


def check_collect_data(output: dict | None, *, store: RunStore, **_: Any) -> list[CheckResult]:
    from ada.schemas import CollectionReport
    res = [_schema("collection_schema", output, CollectionReport)]
    report = output or {}
    if report and not report.get("obtainable", True):
        res.append(fail("data_obtainable", "; ".join(report.get("missing_requirements") or []) or
                        "collector reports the required data is not obtainable", route_hint="define_data"))
    raw = [f for f in store.list_files("raw") if not f["path"].endswith("sources.json")]
    if not raw:
        res.append(fail("raw_files_exist", "no files in raw/", route_hint="define_data"))
        return res
    res.append(ok("raw_files_exist", f"{len(raw)} files"))
    sources = store.read_json("raw/sources.json", default=[]) or []
    by_path = {s.get("path"): s for s in sources}
    missing = [f["path"] for f in raw if f["path"] not in by_path]
    res.append(ok("provenance_recorded") if not missing else
               fail("provenance_recorded", f"files without provenance: {missing[:5]}"))
    bad_hash, no_license = [], []
    for f in raw:
        src = by_path.get(f["path"])
        if not src:
            continue
        if src.get("sha256") != sha256_file(store.resolve(f["path"])):
            bad_hash.append(f["path"])
        if not str(src.get("license", "")).strip() or str(src.get("license")).lower() in ("unknown", "none", "n/a"):
            no_license.append(f["path"])
        for key in ("url", "retrieved_at"):
            if not src.get(key):
                bad_hash.append(f"{f['path']} (missing {key})")
    res.append(ok("hashes_match") if not bad_hash else fail("hashes_match", f"provenance mismatch: {bad_hash[:5]}"))
    res.append(ok("licenses_known") if not no_license else
               fail("licenses_known", f"license unknown for {no_license[:5]} — prefer openly licensed data",
                    severity="warning"))
    primary = report.get("primary_file")
    if primary and not store.exists(primary):
        res.append(fail("primary_file_exists", f"{primary} not found"))
    return res


# ---------------------------------------------------------------------------
def leakage_suspects(df: pd.DataFrame, target: str, threshold: float) -> dict[str, str]:
    """Columns that could leak the target: near-perfect correlation, target-derived names,
    identifier columns, post-hoc information."""
    suspects: dict[str, str] = {}
    if target not in df.columns:
        return suspects
    y = pd.to_numeric(df[target], errors="coerce")
    target_tokens = {t for t in re.split(r"[_\W]+", target.lower()) if len(t) >= 4}
    n = len(df)
    for col in df.columns:
        if col in (target, "_row_id"):
            continue
        lc = col.lower()
        if target_tokens & {t for t in re.split(r"[_\W]+", lc) if len(t) >= 4}:
            suspects[col] = "name derived from the target"
        if any(w in lc for w in POST_HOC_WORDS):
            suspects.setdefault(col, "looks like post-hoc information")
        series = df[col]
        if pd.api.types.is_numeric_dtype(series) and y.notna().sum() > 10:
            x = pd.to_numeric(series, errors="coerce")
            both = x.notna() & y.notna()
            if both.sum() > 10 and x[both].std() > 0 and y[both].std() > 0:
                corr = float(np.corrcoef(x[both], y[both])[0, 1])
                if abs(corr) >= threshold:
                    suspects[col] = f"|corr| with target = {abs(corr):.3f}"
        is_idlike_type = pd.api.types.is_integer_dtype(series) or pd.api.types.is_object_dtype(series) \
            or pd.api.types.is_string_dtype(series)
        if n > 20 and is_idlike_type and series.nunique(dropna=True) >= 0.95 * n:
            if any(w == tok for tok in re.split(r"[_\W]+", lc) for w in ID_WORDS) or pd.api.types.is_object_dtype(series):
                suspects.setdefault(col, "identifier-like (unique per row)")
    return suspects


def _raw_rows(store: RunStore, state: dict) -> int | None:
    primary = ((state.get("phase_outputs") or {}).get("collect_data") or {}).get("primary_file")
    if not primary or not store.exists(primary):
        return None
    path = store.resolve(primary)
    try:
        if path.suffix == ".parquet":
            return len(pd.read_parquet(path))
        if path.suffix in (".csv", ".tsv", ".txt"):
            with open(path, "rb") as fh:
                return max(0, sum(1 for _ in fh) - 1)
        if path.suffix in (".xlsx", ".xls"):
            return len(pd.read_excel(path))
        if path.suffix == ".json":
            return len(pd.read_json(path))
    except Exception:
        return None
    return None


def check_prepare_store(output: dict | None, *, store: RunStore, cfg: Config, state: dict,
                        **_: Any) -> list[CheckResult]:
    from ada.schemas import PrepOutput
    res = [_schema("prep_schema", output, PrepOutput)]
    prep = output or {}
    if prep.get("needs_more_data"):
        res.append(fail("data_sufficient", prep.get("needs_more_data_reason") or "engineer requests more data",
                        route_hint="collect_data"))
    if not store.exists("clean/clean.parquet"):
        res.append(fail("clean_parquet_exists", "clean/clean.parquet missing"))
        return res
    try:
        df = pd.read_parquet(store.resolve("clean/clean.parquet"))
    except Exception as exc:
        res.append(fail("clean_parquet_readable", str(exc)[:300]))
        return res
    res.append(ok("clean_parquet_readable", f"{len(df)} rows x {df.shape[1]} cols"))
    target = prep.get("target_column") or ""
    spec = state.get("objective_spec") or {}
    req = (state.get("phase_outputs") or {}).get("define_data") or {}
    min_rows = int(req.get("min_rows") or cfg.get("gates.min_rows_default", 200))
    # after the holdout lock, clean.parquet only holds the dev rows
    n_total = len(df) + (state.get("holdout_info") or {}).get("n_holdout", 0)
    if "_row_id" not in df.columns:
        res.append(fail("row_id_present", "column _row_id missing"))
    elif df["_row_id"].astype(str).nunique() == 1 and len(df) > 1:
        res.append(fail("row_id_unique", "_row_id is identical for all rows (pass columns, not a DataFrame, to stable_row_id)"))
    elif df["_row_id"].astype(str).duplicated().any():
        res.append(fail("row_id_unique", f"{int(df['_row_id'].astype(str).duplicated().sum())} duplicate _row_id values"))
    else:
        res.append(ok("row_id_unique"))
    if target not in df.columns:
        res.append(fail("target_present", f"target column {target!r} not in clean data"))
        return res
    res.append(ok("target_present", target))
    raw_rows = _raw_rows(store, state)
    if raw_rows and n_total < 0.5 * raw_rows:
        res.append(fail("row_retention", f"clean data keeps {n_total} of {raw_rows} raw rows ({n_total / raw_rows:.0%}) — "
                                         "justify every filter in the data card", severity="warning"))
    if n_total >= min_rows:
        res.append(ok("min_rows", f"{n_total} >= {min_rows}"))
    else:
        if raw_rows is not None and raw_rows >= min_rows:
            # the raw data was big enough: cleaning lost the rows — fix preparation, not collection
            res.append(fail("min_rows", f"cleaning kept only {n_total} of {raw_rows} raw rows (requirement {min_rows}); "
                                        "check filters, dedupe keys and that _row_id differs per row"))
        else:
            res.append(fail("min_rows", f"only {n_total} rows (raw: {raw_rows}), requirement is {min_rows}",
                            route_hint="collect_data"))
    y = df[target]
    t_null = float(y.isna().mean())
    res.append(ok("target_nulls", f"{t_null:.1%}") if t_null <= cfg.get("gates.max_target_null_rate", 0.0)
               else fail("target_nulls", f"target has {t_null:.1%} missing values"))
    if spec.get("task_type") == "regression":
        yn = pd.to_numeric(y, errors="coerce")
        if yn.isna().mean() > 0.0:
            res.append(fail("target_numeric", "regression target is not numeric"))
        elif yn.std() == 0 or yn.nunique() < 5:
            res.append(fail("target_distribution", "target is (almost) constant"))
        else:
            skew_ok = yn.quantile(0.99) < 50 * max(yn.median(), 1e-9) if yn.median() > 0 else True
            res.append(ok("target_distribution", f"median {yn.median():.3g}, p1 {yn.quantile(.01):.3g}, "
                                                 f"p99 {yn.quantile(.99):.3g}") if skew_ok else
                       fail("target_distribution", "extreme target outliers (p99 > 50 x median)", severity="warning"))
    elif spec.get("task_type") == "classification":
        counts = y.astype(str).value_counts(normalize=True)
        res.append(ok("target_classes", counts.round(3).to_dict().__repr__()[:200]) if len(counts) >= 2 else
                   fail("target_classes", "fewer than 2 classes"))
    features = [c for c in df.columns if c not in (target, "_row_id")]
    null_rate = float(df[features].isna().mean().mean()) if features else 1.0
    res.append(ok("null_rate", f"{null_rate:.1%}") if null_rate <= cfg.get("gates.max_null_rate", 0.35) else
               fail("null_rate", f"{null_rate:.1%} of feature cells missing"))
    dup_rate = float(df.drop(columns=["_row_id"], errors="ignore").duplicated().mean())
    res.append(ok("duplicate_rate", f"{dup_rate:.1%}") if dup_rate <= cfg.get("gates.max_duplicate_rate", 0.05) else
               fail("duplicate_rate", f"{dup_rate:.1%} duplicate rows (ignoring _row_id)"))
    res.append(ok("feature_count", str(len(features))) if len(features) >= 2 else
               fail("feature_count", "fewer than 2 feature columns", route_hint="collect_data"))
    suspects = leakage_suspects(df, target, float(cfg.get("gates.leakage_corr_threshold", 0.97)))
    res.append(ok("leakage_scan", "no suspects") if not suspects else
               fail("leakage_scan", "suspects (must not be used as features): " +
                    "; ".join(f"{k}: {v}" for k, v in list(suspects.items())[:8]), severity="warning"))
    res.append(ok("data_card") if store.exists("data_card.md") else fail("data_card", "no data card at path 'data_card.md' (workspace root)"))
    return res


def check_eda(output: dict | None, *, store: RunStore, **_: Any) -> list[CheckResult]:
    from ada.schemas import EDAOutput
    res = [_schema("eda_schema", output, EDAOutput)]
    res.append(ok("eda_report") if store.exists("eda/eda_report.md") else fail("eda_report", "eda/eda_report.md missing"))
    charts = [f for f in store.list_files("eda/charts") if f["path"].endswith(".png")]
    res.append(ok("charts", f"{len(charts)} charts") if charts else fail("charts", "no PNG charts in eda/charts"))
    return res


def check_modeling(output: dict | None, *, store: RunStore, cfg: Config, state: dict,
                   validation: dict | None, holdout_ids: set[str], **_: Any) -> list[CheckResult]:
    from ada.schemas import ModelingOutput
    res = [_schema("modeling_schema", output, ModelingOutput)]
    if not store.exists("models/best_model.pkl"):
        res.append(fail("model_saved", "models/best_model.pkl missing (use ada_kit.save_model)"))
        return res
    meta = store.read_json("models/best_model_meta.json", default={}) or {}
    splits = store.read_json("data/splits.json", default={}) or {}
    train_ids = set(map(str, meta.get("train_row_ids", [])))
    val_ids = set(map(str, splits.get("val_ids", [])))
    if not train_ids:
        res.append(fail("train_ids_recorded", "model meta has no train_row_ids"))
    else:
        overlap = train_ids & val_ids
        res.append(ok("train_val_overlap", "0 rows") if not overlap else
                   fail("train_val_overlap", f"{len(overlap)} validation rows were used for training"))
        hold = train_ids & holdout_ids
        res.append(ok("holdout_untouched") if not hold else
                   fail("holdout_untouched", f"{len(hold)} holdout ids in training ids"))
    experiments = [l for l in (store.read_text("experiments.jsonl") if store.exists("experiments.jsonl") else "").splitlines() if l.strip()]
    res.append(ok("experiments_logged", f"{len(experiments)}") if len(experiments) >= 2 else
               fail("experiments_logged", "log at least a simple baseline and one stronger model"))
    target = (state.get("phase_outputs") or {}).get("prepare_store", {}).get("target_column")
    if store.exists("clean/clean.parquet") and target:
        df = pd.read_parquet(store.resolve("clean/clean.parquet"))
        suspects = leakage_suspects(df, target, float(cfg.get("gates.leakage_corr_threshold", 0.97)))
        used = set(meta.get("features") or (output or {}).get("features_used") or [])
        leaked = {c: suspects[c] for c in used if c in suspects}
        res.append(ok("no_leaky_features") if not leaked else
                   fail("no_leaky_features", "; ".join(f"{k}: {v}" for k, v in leaked.items()),
                        route_hint="prepare_store"))
        if target in used:
            res.append(fail("target_not_feature", "the target is used as a feature"))
    if validation is None:
        res.append(fail("validation_harness", "model could not be scored on the validation split"))
        return res
    metric = (state.get("objective_spec") or {}).get("primary_metric", "mae")
    value, base = validation["metrics"].get(metric), validation["baseline"].get(metric)
    rel = M.relative_improvement(metric, value, base)
    margin = float(cfg.get("gates.must_beat_baseline_by", 0.0))
    res.append(ok("beats_naive_baseline", f"{metric} {value:.4g} vs baseline {base:.4g} ({rel:+.1%})")
               if value is not None and rel > margin else
               fail("beats_naive_baseline", f"{metric} {value} does not beat the naive baseline {base}"))
    return res


def check_evaluation(output: dict | None, *, state: dict, validation: dict | None, **_: Any) -> list[CheckResult]:
    from ada.schemas import EvaluationOutput
    res = [_schema("evaluation_schema", output, EvaluationOutput)]
    spec = state.get("objective_spec") or {}
    metric, thr = spec.get("primary_metric"), spec.get("success_threshold")
    if not validation:
        res.append(fail("model_available", "no validated model — modeling must run first", route_hint="modeling"))
        return res
    value = validation["metrics"].get(metric)
    met = M.meets_threshold(metric, value, thr)
    claimed = (output or {}).get("objective_met")
    if claimed and not met:
        res.append(fail("claim_consistent", f"agent claims success but {metric}={value:.4g} misses threshold {thr}"))
    else:
        res.append(ok("claim_consistent"))
    rec = (output or {}).get("recommendation")
    # After at least two modeling rounds, an honest "present anyway" is allowed (documented shortfall).
    tried_enough = (state.get("visits") or {}).get("modeling", 0) >= 2 and rec == "present"
    if met:
        res.append(ok("threshold_met", f"{metric} {value:.4g} vs {thr}"))
    else:
        res.append(fail("threshold_met", f"{metric} {value:.4g} misses threshold {thr}",
                        severity="warning" if tried_enough else "error",
                        route_hint=rec if rec in ("modeling", "eda") else "modeling"))
    return res


def check_present_results(output: dict | None, *, store: RunStore, **_: Any) -> list[CheckResult]:
    return [ok("report_html") if store.exists("report/final_report.html") else fail("report_html", "missing")]


CHECKS: dict[str, Callable[..., list[CheckResult]]] = {
    "business_objectives": check_business_objectives,
    "define_data": check_define_data,
    "collect_data": check_collect_data,
    "prepare_store": check_prepare_store,
    "eda": check_eda,
    "modeling": check_modeling,
    "evaluation": check_evaluation,
    "present_results": check_present_results,
}


def summarize(checks: list[CheckResult]) -> str:
    return json.dumps([c.to_dict() for c in checks], default=str)

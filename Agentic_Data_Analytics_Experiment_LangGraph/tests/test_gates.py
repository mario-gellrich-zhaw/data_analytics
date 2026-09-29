"""Gate decisions and deterministic checks."""
from __future__ import annotations

import numpy as np
import pandas as pd

from ada.config import Config
from ada.gates.checks import check_evaluation, fail, leakage_suspects, ok
from ada.gates.gate import decide

CFG = Config.load({"budgets": {"max_failed_retries": 2, "max_total_loopbacks": 3, "max_iterations_per_phase": 4}})


def state(**kw):
    base = {"visits": {}, "consecutive_failures": {}, "loopbacks": 0}
    base.update(kw)
    return base


def test_pass_offers_forward_edges():
    g = decide("prepare_store", [ok("a")], {"score": 8, "verdict": "accept"}, state(visits={"prepare_store": 1}), CFG)
    assert g.decision == "pass" and g.options == ["eda", "modeling"]


def test_low_critic_score_retries():
    g = decide("eda", [ok("a")], {"score": 3, "verdict": "revise"}, state(visits={"eda": 1}), CFG)
    assert g.decision == "retry_same_phase" and g.options == ["eda"]


def test_hard_failure_with_route_hint_routes_back():
    g = decide("prepare_store", [fail("min_rows", "too few", route_hint="collect_data")], None,
               state(visits={"prepare_store": 1}), CFG)
    assert g.decision == "route_back" and g.options == ["collect_data"]


def test_strategy_changes_after_two_failed_retries():
    st = state(visits={"modeling": 3}, consecutive_failures={"modeling": 2})
    g = decide("modeling", [fail("beats_naive_baseline", "no")], None, st, CFG)
    assert g.decision == "route_back" and "modeling" not in g.options and g.options == ["prepare_store"]


def test_critic_only_objection_passes_with_warnings_after_retries():
    st = state(visits={"eda": 3}, consecutive_failures={"eda": 2})
    g = decide("eda", [ok("a")], {"score": 4, "verdict": "revise"}, st, CFG)
    assert g.decision == "pass"


def test_escalates_when_loopbacks_exhausted():
    st = state(visits={"evaluation": 2}, loopbacks=3)
    g = decide("evaluation", [fail("threshold_met", "missed", route_hint="modeling")], None, st, CFG)
    assert g.decision == "escalate" and g.options == ["present_results"]


def test_iteration_cap_excludes_node():
    st = state(visits={"prepare_store": 1, "eda": 4})
    g = decide("prepare_store", [ok("a")], None, st, CFG)
    assert g.options == ["modeling"]


def test_leakage_scan_flags_target_copies_and_ids():
    rng = np.random.default_rng(1)
    n = 300
    df = pd.DataFrame({"_row_id": range(n), "rent": rng.normal(2000, 400, n)})
    df["rent_per_m2"] = df["rent"] / 70
    df["area"] = rng.normal(70, 10, n)
    df["listing_url"] = [f"https://x/{i}" for i in range(n)]
    s = leakage_suspects(df, "rent", 0.97)
    assert "rent_per_m2" in s and "listing_url" in s and "area" not in s


def test_evaluation_claim_must_match_numbers():
    st = {"objective_spec": {"primary_metric": "mae", "success_threshold": 100}, "visits": {"modeling": 1}}
    val = {"metrics": {"mae": 150.0}, "baseline": {"mae": 300.0}}
    checks = check_evaluation({"objective_met": True, "summary": "great", "recommendation": "present"},
                              state=st, validation=val)
    failed = {c.name for c in checks if not c.passed and c.severity == "error"}
    assert {"claim_consistent", "threshold_met"} <= failed


def test_giving_up_without_attempts_forces_collection_retry(tmp_path):
    from ada.gates.checks import check_collect_data
    from ada.store import RunStore
    store = RunStore("x", root=tmp_path).init()
    report = {"files": [], "primary_file": "", "license_assessment": "n/a", "obtainable": False,
              "missing_requirements": ["no open data"]}
    lazy = check_collect_data(report, store=store, attempts=0)
    assert any(c.name == "collection_effort" and not c.passed and c.route_hint is None for c in lazy)
    assert all(c.route_hint is None for c in lazy)       # nothing may send it upstream yet
    tried = check_collect_data(report, store=store, attempts=4)
    assert any(c.name == "data_obtainable" and c.route_hint == "define_data" for c in tried)


def test_more_data_request_with_enough_rows_does_not_reroute(tmp_path):
    from ada.gates.checks import check_prepare_store
    from ada.store import RunStore
    store = RunStore("y", root=tmp_path).init()
    n = 600
    rng = np.random.default_rng(0)
    pd.DataFrame({"_row_id": [f"r{i}" for i in range(n)], "rent": rng.normal(2000, 300, n),
                  "area": rng.normal(70, 10, n), "rooms": rng.integers(1, 6, n)}).to_parquet(store.resolve("clean/clean.parquet"))
    store.write_text("data_card.md", "card")
    prep = {"target_column": "rent", "feature_columns": ["area", "rooms"], "row_count": n, "cleaning_steps": ["x"],
            "needs_more_data": True, "needs_more_data_reason": "no postcode"}
    st = {"objective_spec": {"task_type": "regression"}, "phase_outputs": {"define_data": {"min_rows": 500}}}
    checks = check_prepare_store(prep, store=store, cfg=CFG, state=st)
    assert all(c.route_hint is None for c in checks)
    assert any(c.name == "data_sufficient" and c.severity == "warning" for c in checks)
    st["phase_outputs"]["define_data"]["min_rows"] = 5000          # genuinely too few rows -> re-collect
    checks = check_prepare_store(prep, store=store, cfg=CFG, state=st)
    assert any(c.route_hint == "collect_data" for c in checks)


def test_raw_row_count_handles_multiline_csv(tmp_path):
    from ada.gates.checks import _raw_rows
    from ada.store import RunStore
    store = RunStore("z", root=tmp_path).init()
    store.write_text("raw/t.csv", 'id,text\n1,"line one\nline two\nline three"\n2,"ok"\n')
    assert _raw_rows(store, {"phase_outputs": {"collect_data": {"primary_file": "raw/t.csv"}}}) == 2

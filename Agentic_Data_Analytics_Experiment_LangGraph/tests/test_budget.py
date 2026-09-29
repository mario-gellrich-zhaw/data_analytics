"""Budgets stop a run: the tracker refuses further LLM calls, the graph escalates
to present_results and finishes with a template report."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from ada.budget import BudgetExceeded, BudgetTracker
from ada.config import Config
from ada.runner import RunManager


def test_tracker_raises_when_exhausted():
    cfg = Config.load({"budgets": {"max_usd": 0.01, "presenter_reserve_usd": 0}})
    t = BudgetTracker(cfg)
    t.check()
    t.record(agent="x", model="gpt-5.1", input_tokens=10_000, cached_tokens=0, output_tokens=0)  # $0.0125
    with pytest.raises(BudgetExceeded):
        t.check()


def test_reserve_is_kept_for_presenter():
    cfg = Config.load({"budgets": {"max_usd": 1.0, "presenter_reserve_usd": 0.2}})
    t = BudgetTracker(cfg)
    t.add_usd(0.85)
    with pytest.raises(BudgetExceeded):
        t.check()
    t.check(use_reserve=True)          # the presenter may still spend the reserve


def test_token_and_time_limits():
    cfg = Config.load({"budgets": {"max_tokens": 100, "max_wall_seconds": 3600, "presenter_reserve_usd": 0}})
    t = BudgetTracker(cfg)
    t.record(agent="x", model="gpt-5-mini", input_tokens=90, cached_tokens=0, output_tokens=20)
    assert "token" in t.exhausted_reason()
    t2 = BudgetTracker(Config.load({"budgets": {"max_wall_seconds": 5, "presenter_reserve_usd": 0}}),
                       snapshot={"elapsed_seconds": 10})
    assert "wall-time" in t2.exhausted_reason()


def test_stub_run_stops_on_budget():
    mgr = RunManager()
    run_id = mgr.start("budget test", None, {"mode": "stub", "stub_delay": 0,
                                             "budgets": {"max_usd": 0.1, "presenter_reserve_usd": 0}},
                       background=False)
    run = mgr.events.get_run(run_id)
    assert run["status"] == "budget_exhausted"
    state = mgr.final_state(run_id)
    assert state["stop_reason"] and "USD" in state["stop_reason"]
    assert state["visits"].get("present_results") == 1
    assert not state["visits"].get("modeling")          # never got that far
    escalations = [e for e in mgr.events.events(run_id, types=["gate"]) if e["payload"].get("decision") == "escalate"]
    assert escalations


class _FakeResponses:
    """Always asks for another tool call and reports 1M input tokens per call."""
    def __init__(self):
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        call = SimpleNamespace(type="function_call", name="list_files", arguments=json.dumps({"subdir": "."}),
                               call_id=f"c{self.calls}")
        usage = SimpleNamespace(input_tokens=1_000_000, output_tokens=0,
                                input_tokens_details=SimpleNamespace(cached_tokens=0))
        return SimpleNamespace(output=[call], usage=usage, output_text="")


def test_real_agents_stop_when_llm_budget_is_spent():
    fake = _FakeResponses()
    mgr = RunManager()
    run_id = mgr.start("objective that never ends", None,
                       {"mode": "real", "offline": True, "_llm_client": SimpleNamespace(responses=fake),
                        "budgets": {"max_usd": 3.0, "presenter_reserve_usd": 0}}, background=False)
    run = mgr.events.get_run(run_id)
    assert run["status"] == "budget_exhausted"
    assert fake.calls == 3                         # $1.25 per call with gpt-5.1 -> third call crosses $3
    state = mgr.final_state(run_id)
    assert state["visits"] == {"business_objectives": 1, "present_results": 1}
    report = mgr.events.events(run_id, types=["artifact"])
    assert any(e["payload"].get("path") == "report/final_report.html" for e in report)

"""The LangGraph wiring matches the process model, including feedback edges."""
from __future__ import annotations

from ada.graph.build import build_graph
from ada.graph.topology import FEEDBACK, FORWARD, NODES, allowed_targets, edge_kind
from ada.runner import RunManager

SPEC_FORWARD = {("business_objectives", "define_data"), ("define_data", "collect_data"),
                ("collect_data", "prepare_store"), ("prepare_store", "eda"), ("prepare_store", "modeling"),
                ("eda", "evaluation"), ("modeling", "evaluation"), ("evaluation", "present_results")}
SPEC_FEEDBACK = {("define_data", "business_objectives"), ("collect_data", "define_data"),
                 ("prepare_store", "collect_data"), ("eda", "prepare_store"), ("modeling", "prepare_store"),
                 ("evaluation", "eda"), ("evaluation", "modeling")}


def test_topology_matches_process_model():
    forward = {(s, t) for s, ts in FORWARD.items() for t in ts}
    feedback = {(s, t) for s, ts in FEEDBACK.items() for t in ts}
    assert forward == SPEC_FORWARD
    assert feedback == SPEC_FEEDBACK
    # both directions of every "<->" pair exist
    for a, b in [("eda", "prepare_store"), ("modeling", "prepare_store"), ("evaluation", "eda"), ("evaluation", "modeling")]:
        assert (a, b) in forward | feedback and (b, a) in forward | feedback


def test_compiled_graph_has_real_conditional_edges(ctx):
    graph = build_graph(ctx).get_graph()
    edges = {(e.source, e.target, e.conditional) for e in graph.edges}
    for s, t in SPEC_FORWARD | SPEC_FEEDBACK:
        assert (s, t, True) in edges, f"missing conditional edge {s}->{t}"
    # nothing outside the allowed set (forward, feedback, retry, escalate->present_results)
    for e in graph.edges:
        if e.source in NODES and e.source != "present_results":
            assert e.target in allowed_targets(e.source)


def test_edge_kinds():
    assert edge_kind("eda", "prepare_store") == "feedback"
    assert edge_kind("prepare_store", "eda") == "forward"
    assert edge_kind("modeling", "modeling") == "retry"
    assert edge_kind("collect_data", "present_results") == "escalate"


def test_stub_run_traverses_feedback_edges():
    mgr = RunManager()
    run_id = mgr.start("demo objective", "Testland", {"mode": "stub", "stub_delay": 0}, background=False)
    run = mgr.events.get_run(run_id)
    assert run["status"] == "completed"
    transitions = [e["payload"] for e in mgr.events.events(run_id, types=["transition"])]
    pairs = [(p.get("from"), p.get("to"), p.get("kind")) for p in transitions]
    assert ("eda", "prepare_store", "feedback") in pairs
    assert ("evaluation", "modeling", "feedback") in pairs
    assert pairs[-1][2] == "end"
    gates = mgr.events.events(run_id, types=["gate"])
    assert all("decision" in g["payload"] for g in gates)
    assert mgr.final_state(run_id)["loopbacks"] == 2


def test_human_approval_pauses_and_resumes():
    mgr = RunManager()
    run_id = mgr.start("approval test", None, {"mode": "stub", "stub_delay": 0, "human_approval": True},
                       background=False)
    assert mgr.events.get_run(run_id)["status"] == "awaiting_approval"
    pending = mgr.pending_interrupt(run_id)
    assert pending["node"] == "business_objectives" and pending["proposed"] == "define_data"
    started = len(mgr.events.events(run_id, types=["message"]))
    mgr.resume(run_id, background=False, resume_value={"next_node": "define_data", "note": "ok"})
    # the approved phase was not re-run (its work is cached per visit)
    assert mgr.final_state(run_id)["visits"]["business_objectives"] == 1
    assert mgr.events.get_run(run_id)["status"] == "awaiting_approval"
    assert mgr.pending_interrupt(run_id)["node"] == "define_data"
    assert len(mgr.events.events(run_id, types=["message"])) > started


def test_resume_after_crash_continues_from_checkpoint(monkeypatch):
    import ada.graph.stub as stub
    original = stub.stub_phase
    calls = {"n": 0}

    def flaky(ctx, state, node):
        if node == "eda" and calls["n"] == 0:
            calls["n"] += 1
            raise KeyboardInterrupt("simulated crash")   # not caught by the node wrapper
        return original(ctx, state, node)

    monkeypatch.setattr("ada.graph.build.stub_phase", flaky)
    mgr = RunManager()
    try:
        mgr.start("crash test", None, {"mode": "stub", "stub_delay": 0}, background=False, run_id="crash-test-1")
    except KeyboardInterrupt:
        pass
    state = mgr.final_state("crash-test-1")
    assert state["next"] == "eda" and state["visits"]["prepare_store"] == 1
    mgr.resume("crash-test-1", background=False)
    assert mgr.events.get_run("crash-test-1")["status"] == "completed"
    final = mgr.final_state("crash-test-1")
    assert final["visits"]["collect_data"] == 1          # earlier phases were not repeated


def test_delete_run_removes_everything():
    from ada.paths import vault_root
    from ada.store import RunStore
    mgr = RunManager()
    run_id = mgr.start("delete me", None, {"mode": "stub", "stub_delay": 0}, background=False)
    (vault_root() / run_id).mkdir(parents=True, exist_ok=True)
    assert RunStore(run_id).root.exists()
    mgr.delete(run_id)
    assert mgr.events.get_run(run_id) is None
    assert mgr.events.events(run_id) == []
    assert not RunStore(run_id).root.exists() and not (vault_root() / run_id).exists()
    assert not mgr.final_state(run_id)       # checkpoints gone

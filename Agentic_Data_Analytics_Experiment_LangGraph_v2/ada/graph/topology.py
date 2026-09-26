"""The process model — single source of truth for the LangGraph wiring, the
gates' allowed routes, and the UI layout. Protected from the improver."""
from __future__ import annotations

START_NODE = "business_objectives"
END_NODE = "present_results"

NODES: list[str] = ["business_objectives", "define_data", "collect_data", "prepare_store",
                    "eda", "modeling", "evaluation", "present_results"]

LABELS = {
    "business_objectives": "Business objectives",
    "define_data": "Define data",
    "collect_data": "Collect data",
    "prepare_store": "Prepare & store",
    "eda": "Exploratory data analysis",
    "modeling": "Modeling",
    "evaluation": "Evaluation",
    "present_results": "Present results",
}

AGENTS = {
    "business_objectives": "ObjectiveAgent",
    "define_data": "DataRequirementsAgent",
    "collect_data": "DataCollectorAgent",
    "prepare_store": "DataEngineerAgent",
    "eda": "EDAAgent",
    "modeling": "ModelingAgent",
    "evaluation": "EvaluationAgent",
    "present_results": "PresenterAgent",
}

# forward flow
FORWARD: dict[str, list[str]] = {
    "business_objectives": ["define_data"],
    "define_data": ["collect_data"],
    "collect_data": ["prepare_store"],
    "prepare_store": ["eda", "modeling"],
    "eda": ["evaluation"],
    "modeling": ["evaluation"],
    "evaluation": ["present_results"],
    "present_results": [],
}

# feedback edges (conditional)
FEEDBACK: dict[str, list[str]] = {
    "business_objectives": [],
    "define_data": ["business_objectives"],
    "collect_data": ["define_data"],
    "prepare_store": ["collect_data"],
    "eda": ["prepare_store"],
    "modeling": ["prepare_store"],
    "evaluation": ["eda", "modeling"],
    "present_results": [],
}

# EDA and modeling alternate through prepare_store: after an EDA pass the next
# step may be prepare_store (apply findings) as well as evaluation.
PASS_EXTRA: dict[str, list[str]] = {"eda": ["prepare_store"]}


def allowed_targets(node: str) -> list[str]:
    """Every edge a node may take: forward, feedback, retry (self) and escalation."""
    if node == END_NODE:
        return []
    targets = FORWARD[node] + FEEDBACK[node] + [node]
    if END_NODE not in targets:
        targets.append(END_NODE)   # escalate / budget exhausted -> present best so far
    seen: list[str] = []
    for t in targets:
        if t not in seen:
            seen.append(t)
    return seen


def edge_kind(src: str, dst: str) -> str:
    if src == dst:
        return "retry"
    if dst in FORWARD.get(src, []):
        return "forward"
    if dst in FEEDBACK.get(src, []):
        return "feedback"
    if dst == END_NODE:
        return "escalate"
    return "other"


# UI layout (left-to-right like the process model diagram; EDA above, modeling below)
LAYOUT = {
    "business_objectives": (0, 150),
    "define_data": (230, 150),
    "collect_data": (460, 150),
    "prepare_store": (690, 150),
    "eda": (930, 30),
    "modeling": (930, 270),
    "evaluation": (1170, 150),
    "present_results": (1400, 150),
}


def graph_spec() -> dict:
    edges = []
    for src in NODES:
        for dst in FORWARD[src]:
            edges.append({"id": f"{src}->{dst}", "source": src, "target": dst, "kind": "forward"})
        for dst in FEEDBACK[src]:
            edges.append({"id": f"{src}->{dst}", "source": src, "target": dst, "kind": "feedback"})
    return {
        "nodes": [{"id": n, "label": LABELS[n], "agent": AGENTS[n], "x": LAYOUT[n][0], "y": LAYOUT[n][1]}
                  for n in NODES],
        "edges": edges,
    }

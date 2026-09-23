"""LangGraph-based agentic orchestration: a persona, a shared growing
transcript (nobody has private memory), and the model deciding for itself,
turn by turn, whether to call a real tool (`tool_choice="auto"` applies;
nothing here forces a tool) — expressed as a small LangGraph `StateGraph`:

  agent_turn -> (tool_calls?) -> tools (up to 3 chained rounds) -> record_turn -> loop or end
                              -> finish_turn -^

`agent_turn` asks the current agent to speak. If it asked for a real tool,
`tools` actually calls it (the same Python functions in data_tool.py) and
then asks the model to react to the real result in one short sentence.
Either way, `record_turn` strips the status tag, appends the clean reply to
the shared transcript, and a conditional edge decides whether to loop back
for the next agent's turn or end the phase (turn cap hit, or everyone said
via their status tag that this step is genuinely done).

One compiled graph is reused for every phase of the conversation — only the
*initial state* changes (which agents participate, whether tools are
enabled, the turn budget).
"""

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph
from typing_extensions import TypedDict

STATUS_TAG_RE = re.compile(r"\s*\[STATUS:\s*(CONTINUE|NEXT)\]\s*$", re.IGNORECASE)
# Guardrail: strip a name the model sometimes mimics onto the front of its
# own reply before it lands in the shared transcript and the pattern
# self-reinforces.
SPEAKER_PREFIX_RE = re.compile(
    r"^(Product Manager|Data Analyst|Data Engineer)\s*:\s*", re.IGNORECASE
)

# One turn may chain a few tool rounds (e.g. write_scraper_code, then
# run_scraper on it, then preview_data) before the agent has to say
# something; past that, it's asked for plain text only.
MAX_TOOL_ROUNDS = 3

REACT_PROMPT = (
    "If this result means you should immediately use another tool (e.g. run "
    "the scraper you just wrote, or fix and rewrite it), call it now. "
    "Otherwise react to that real result in ONE short sentence (max ~15 words). Do not "
    "list individual items or repeat numbers already in the result — they're "
    "shown separately in the UI."
)


@dataclass
class AgentConfig:
    """One agent's persona + its (optionally tool-bound) chat model for the
    CURRENT phase — the LangGraph analogue of agents.py's `Agent`, minus the
    turn-taking logic, which now lives in the graph itself."""

    name: str
    persona: str
    model: Any  # a langchain_openai.ChatOpenAI, optionally `.bind_tools(...)`-ed
    # Private working memory: called before every turn; any text it returns
    # is shown only to this agent (e.g. its last scraper run's real error and
    # response structure, which the one-sentence shared transcript can't hold).
    working_notes: Callable[[], str] | None = None


def _messages_for(cfg: AgentConfig, transcript: list[dict]) -> list:
    """This agent's view of the shared transcript: its own past turns look
    like its own (assistant) messages, everyone else's look like a peer
    talking ('Name: text') — identical logic to agents.py's Agent.speak."""
    messages: list = [SystemMessage(content=cfg.persona)]
    for entry in transcript:
        if entry["speaker"] == cfg.name:
            messages.append(AIMessage(content=entry["text"]))
        else:
            messages.append(HumanMessage(content=f'{entry["speaker"]}: {entry["text"]}'))
    notes = cfg.working_notes() if cfg.working_notes else ""
    if notes:
        messages.append(SystemMessage(content=notes))
    return messages


class PhaseState(TypedDict):
    """Shared state for one phase's conversation loop."""

    transcript: list[dict]
    phase_agents: list[AgentConfig]
    tool_impls: dict[str, Callable]
    turn_idx: int
    turns_in_phase: int
    min_turns: int
    max_turns: int
    ready: dict[str, bool]
    # scratch fields: written by agent_turn/tools/finish_turn, consumed by record_turn
    pending_speaker: str
    pending_messages: list
    pending_ai_message: Any
    final_text: str
    used_tool: bool
    # written by record_turn, read by the driver (server.py) after every step
    last_turn: dict | None


def _current_agent(state: PhaseState) -> AgentConfig:
    agents = state["phase_agents"]
    return agents[state["turn_idx"] % len(agents)]


def agent_turn(state: PhaseState) -> dict:
    """The current agent sees the shared transcript and replies."""
    cfg = _current_agent(state)
    messages = _messages_for(cfg, state["transcript"])
    ai_message = cfg.model.invoke(messages)
    return {
        "pending_speaker": cfg.name,
        "pending_messages": messages,
        "pending_ai_message": ai_message,
    }


def route_after_agent_turn(state: PhaseState) -> str:
    """Whether the agent's reply asked for a real tool call."""
    return "tools" if state["pending_ai_message"].tool_calls else "finish_turn"


def _tools_then_react(
    cfg: AgentConfig, messages: list, ai_message: AIMessage, tool_impls: dict[str, Callable]
) -> str:
    """Really call the tool(s) the agent asked for and ask it to react to
    the real result. If its reaction is another tool call (e.g. running
    the scraper it just wrote), that runs too — up to MAX_TOOL_ROUNDS —
    and the last reaction is forced to be plain text. Kept ephemeral: none
    of this lands in the shared transcript, only the returned text does."""
    messages = list(messages)
    for tool_round in range(1, MAX_TOOL_ROUNDS + 1):
        messages.append(ai_message)
        for call in ai_message.tool_calls:
            fn = tool_impls[call["name"]]
            result = fn(**call["args"])
            messages.append(ToolMessage(content=json.dumps(result), tool_call_id=call["id"]))
        messages.append(HumanMessage(content=REACT_PROMPT))
        ai_message = cfg.model.invoke(messages)
        if not ai_message.tool_calls:
            return ai_message.content
        if tool_round == MAX_TOOL_ROUNDS:
            break
    return cfg.model.invoke(messages, tool_choice="none").content


def run_tools(state: PhaseState) -> dict:
    """Really call whichever tool(s) the agent asked for, then let it react."""
    cfg = _current_agent(state)
    final_text = _tools_then_react(
        cfg, state["pending_messages"], state["pending_ai_message"], state["tool_impls"]
    )
    return {"final_text": final_text, "used_tool": True}


def finish_turn(state: PhaseState) -> dict:
    """No tool was requested — the agent's own reply is the final text."""
    return {"final_text": state["pending_ai_message"].content, "used_tool": False}


def record_turn(state: PhaseState) -> dict:
    """Strip the status tag / any accidental name prefix, append the clean
    reply to the shared transcript, and update this agent's readiness."""
    speaker = state["pending_speaker"]
    raw = state["final_text"] or ""
    match = STATUS_TAG_RE.search(raw)
    status = match.group(1).upper() if match else "CONTINUE"
    clean = STATUS_TAG_RE.sub("", raw).strip()
    clean = SPEAKER_PREFIX_RE.sub("", clean)

    ready = dict(state["ready"])
    ready[speaker] = status == "NEXT"
    transcript = state["transcript"] + [{"speaker": speaker, "text": clean}]

    return {
        "ready": ready,
        "transcript": transcript,
        "turns_in_phase": state["turns_in_phase"] + 1,
        "turn_idx": state["turn_idx"] + 1,
        "last_turn": {"speaker": speaker, "text": clean, "used_tool": state["used_tool"]},
    }


def route_after_record_turn(state: PhaseState) -> str:
    """Loop back for the next agent's turn, or end the phase: either the
    phase's own turn cap was hit, or every participating agent has said,
    via its status tag, that it's genuinely done (and the minimum has
    already been met, so nobody can end a phase on turn one)."""
    if state["turns_in_phase"] >= state["max_turns"]:
        return END
    if state["turns_in_phase"] >= state["min_turns"] and all(state["ready"].values()):
        return END
    return "agent_turn"


def build_phase_graph():
    """Compile the small state graph that drives ONE phase's conversation.
    Reused for every phase of the run — server.py's `run_phase()` just
    streams it with a different initial state each time."""
    builder = StateGraph(PhaseState)
    builder.add_node("agent_turn", agent_turn)
    builder.add_node("tools", run_tools)
    builder.add_node("finish_turn", finish_turn)
    builder.add_node("record_turn", record_turn)

    builder.set_entry_point("agent_turn")
    builder.add_conditional_edges(
        "agent_turn", route_after_agent_turn, {"tools": "tools", "finish_turn": "finish_turn"}
    )
    builder.add_edge("tools", "record_turn")
    builder.add_edge("finish_turn", "record_turn")
    builder.add_conditional_edges(
        "record_turn", route_after_record_turn, {"agent_turn": "agent_turn", END: END}
    )
    return builder.compile()


def speak_once(
    cfg: AgentConfig, transcript: list[dict], tool_impls: dict[str, Callable]
) -> tuple[str, bool]:
    """A single reply outside a full phase loop (e.g. the fixed business
    objective's one-line acknowledgment) — the same model-then-maybe-tool
    logic as agent_turn/run_tools/finish_turn, without a turn-taking loop
    around it. Returns the RAW reply (status tag / prefix not stripped —
    same contract as agents.py's Agent.speak(); the caller strips it)."""
    messages = _messages_for(cfg, transcript)
    ai_message = cfg.model.invoke(messages)
    if not ai_message.tool_calls:
        return ai_message.content, False
    return _tools_then_react(cfg, messages, ai_message, tool_impls), True

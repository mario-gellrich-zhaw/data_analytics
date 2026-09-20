"""A minimal, transparent agentic framework for teaching purposes.

No external agent library (no LangChain, CrewAI, AutoGen, ...) is used on
purpose: every mechanism an "agent" needs is spelled out here in plain
Python so students can read the whole thing in a few minutes:

- **Persona**: a fixed system prompt that defines the agent's role
- **Shared memory**: both agents see the *same* growing transcript, not two
  separate one-on-one threads — so neither agent "forgets" what the other
  already found out (e.g. which platforms were already tried and blocked)
- **Tool use**: an agent can be given real Python functions it may call
  (OpenAI "function calling") to actually *act*, not just talk — and it
  decides for itself, turn by turn, whether and which tool to use
  (`tool_choice="auto"` everywhere; nothing is forced by the caller)
"""

import json

from openai import OpenAI


class Agent:
    """A chat participant with a persona and optional tools.

    Unlike a simple two-party chatbot, this agent doesn't keep its own
    private history. Instead, `speak()` is given the *shared* transcript
    (every message from both agents so far) each time it's this agent's
    turn, so it always has the full picture.
    """

    def __init__(
        self,
        client: OpenAI,
        name: str,
        persona: str,
        model: str = "gpt-4o-mini",
        tools: list | None = None,
        tool_impls: dict | None = None,
    ):
        self.client = client
        self.name = name
        self.persona = persona
        self.model = model
        self.tools = tools
        self.tool_impls = tool_impls or {}

    def _messages_for(self, transcript: list[dict]) -> list[dict]:
        messages = [{"role": "system", "content": self.persona}]
        for entry in transcript:
            if entry["speaker"] == self.name:
                messages.append({"role": "assistant", "content": entry["text"]})
            else:
                messages.append({"role": "user", "content": f'{entry["speaker"]}: {entry["text"]}'})
        return messages

    def speak(self, transcript: list[dict]) -> tuple[str, bool]:
        """Produce this agent's next message, given the shared transcript so far.

        Returns `(reply_text, used_tool)`. The model decides on its own
        whether to call a tool (`tool_choice="auto"`) — the caller doesn't
        force any particular step.
        """
        messages = self._messages_for(transcript)

        completion = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self.tools if self.tools else None,
            tool_choice="auto" if self.tools else None,
        )
        message = completion.choices[0].message

        if not message.tool_calls:
            return message.content, False

        # Record the assistant's tool-call request, then execute each tool
        # locally and feed the result back as a "tool" message.
        messages.append(message)
        for call in message.tool_calls:
            func = self.tool_impls[call.function.name]
            args = json.loads(call.function.arguments or "{}")
            result = func(**args)
            messages.append(
                {"role": "tool", "tool_call_id": call.id, "content": json.dumps(result)}
            )

        # Ask the model to react to the real result in natural language.
        # This "keep it brief" nudge is ephemeral (not saved to the shared
        # transcript): the raw structured result is shown separately in the
        # UI, so the spoken reply only needs to react to it, not restate it.
        follow_up = self.client.chat.completions.create(
            model=self.model,
            messages=messages
            + [
                {
                    "role": "user",
                    "content": (
                        "React to that real result in ONE short sentence (max "
                        "~15 words), unless you're in the middle of a detailed "
                        "data-quality discussion, in which case 2-3 sentences "
                        "are fine. Do not list individual items — they're "
                        "shown separately in the UI."
                    ),
                }
            ],
        )
        return follow_up.choices[0].message.content, True

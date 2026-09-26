"""Tool-calling agent loop on the OpenAI Responses API.

Every agent = versioned system prompt (prompts/*.md) + fixed tool whitelist +
Pydantic output schema. The agent ends by calling `submit_result` with an object
matching its schema; invalid submissions are bounced back with the validation
error. Every message, tool call and tool result is emitted as an event.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel, ValidationError

from ada.paths import prompts_dir
from agents.tools import REGISTRY, ToolContext, openai_spec, submit_spec, to_text


class AgentFailed(RuntimeError):
    pass


class RunStopped(RuntimeError):
    pass


def load_prompt(name: str) -> tuple[str, str]:
    """Return (text, version) of prompts/<name>.md; front matter holds `version:`."""
    path = prompts_dir() / f"{name}.md"
    raw = path.read_text(encoding="utf-8")
    version = "0"
    m = re.match(r"^---\n(.*?)\n---\n", raw, re.S)
    if m:
        for line in m.group(1).splitlines():
            if line.startswith("version:"):
                version = line.split(":", 1)[1].strip()
        raw = raw[m.end():]
    return raw.strip(), version


class Agent:
    name: ClassVar[str] = "Agent"
    role: ClassVar[str] = "worker"            # key into config `roles`
    prompt: ClassVar[str] = ""                # prompts/<prompt>.md
    tools: ClassVar[list[str]] = []           # fixed whitelist
    output_model: ClassVar[type[BaseModel]]
    submit_description: ClassVar[str] = "Submit your final result. Call exactly once, when done."
    use_reserve: bool = False                 # presenter may spend the budget reserve

    def __init__(self, ctx: Any, node: str):
        self.ctx = ctx
        self.node = node

    # ------------------------------------------------------------------
    def instructions(self) -> tuple[str, str]:
        common, common_v = load_prompt("_common")
        own, own_v = load_prompt(self.prompt)
        return f"{common}\n\n{own}", f"{self.prompt}@{own_v}+common@{common_v}"

    def allowed_tools(self) -> list[str]:
        names = [n for n in self.tools if n in REGISTRY]
        if self.ctx.offline:
            names = [n for n in names if not REGISTRY[n].network]
        return names

    def max_steps(self) -> int:
        cfg = self.ctx.cfg
        return int(cfg.get(f"agents.max_tool_steps.{self.role_key()}", cfg.get("agents.max_tool_steps.default", 16)))

    def role_key(self) -> str:
        return self.role

    def emit(self, type: str, summary: str, payload: dict[str, Any] | None = None) -> None:
        self.ctx.emit(type, summary, node=self.node, agent=self.name, payload=payload)

    # ------------------------------------------------------------------
    def run(self, task: str, *, max_steps: int | None = None) -> BaseModel:
        cfg = self.ctx.cfg
        instructions, prompt_version = self.instructions()
        tool_names = self.allowed_tools()
        specs = [openai_spec(REGISTRY[n]) for n in tool_names] + [submit_spec(self.output_model, self.submit_description)]
        steps = max_steps or self.max_steps()
        keep_full = int(cfg.get("agents.keep_full_tool_outputs", 6))
        short = int(cfg.get("agents.truncated_tool_output_chars", 600))
        limit = int(cfg.get("agents.max_tool_output_chars", 6000))
        self.emit("message", f"{self.name} started ({cfg.model_for(self.role)}, prompt {prompt_version})",
                  {"kind": "agent_start", "prompt_version": prompt_version, "tools": tool_names,
                   "task": task[:4000]})

        items: list[Any] = [{"role": "user", "content": task}]
        outputs: list[dict[str, Any]] = []        # our function_call_output dicts (for truncation)
        nudges = 0
        for step in range(steps):
            if self.ctx.stop_requested.is_set():
                raise RunStopped("stop requested")
            last = step == steps - 1
            choice: Any = {"type": "function", "name": "submit_result"} if last else "auto"
            resp = self.ctx.llm.create(role=self.role, agent=self.name, node=self.node, instructions=instructions,
                                       input=items, tools=specs, tool_choice=choice,
                                       use_reserve=self.use_reserve)
            items.extend(resp.output)
            calls = [o for o in resp.output if getattr(o, "type", "") == "function_call"]
            for o in resp.output:
                if getattr(o, "type", "") == "message":
                    text = "".join(getattr(p, "text", "") for p in (o.content or [])).strip()
                    if text:
                        self.emit("message", text[:1500], {"kind": "agent_text", "step": step})
            if not calls:
                nudges += 1
                if nudges > 3:
                    break
                items.append({"role": "user", "content": "Continue: call a tool, or call submit_result with your final output."})
                continue
            for call in calls:
                if call.name == "submit_result":
                    try:
                        result = self.output_model.model_validate_json(call.arguments)
                    except ValidationError as exc:
                        msg = f"submit_result rejected — fix these fields and submit again:\n{exc}"
                        self.emit("message", "submission rejected by schema validation", {"kind": "schema_error",
                                                                                          "error": str(exc)[:1500]})
                        out = {"type": "function_call_output", "call_id": call.call_id, "output": msg[:3000]}
                        items.append(out)
                        continue
                    self.emit("message", f"{self.name} submitted its result", {"kind": "agent_result",
                                                                              "result": result.model_dump()})
                    return result
                out = {"type": "function_call_output", "call_id": call.call_id,
                       "output": self._execute(call.name, call.arguments, tool_names, limit)}
                items.append(out)
                outputs.append(out)
            for old in outputs[:-keep_full]:
                if len(old["output"]) > short:
                    old["output"] = old["output"][:short] + "\n...[older tool output truncated]"
        raise AgentFailed(f"{self.name} did not submit a valid result within {steps} steps")

    def _execute(self, name: str, arguments: str, allowed: list[str], limit: int) -> str:
        if name not in allowed:
            self.emit("tool_result", f"{name}: not allowed for {self.name}", {"tool": name, "ok": False})
            return f"error: tool {name!r} is not available to you"
        tool = REGISTRY[name]
        try:
            args_obj = tool.params.model_validate_json(arguments or "{}")
        except ValidationError as exc:
            return f"error: invalid arguments: {exc}"[:2000]
        shown = args_obj.model_dump()
        if "code" in shown:
            shown["code"] = shown["code"][:6000]
        self.emit("tool_call", f"{name}({_short_args(shown)})", {"tool": name, "args": shown})
        started = time.monotonic()
        try:
            result = tool.fn(ToolContext(self.ctx, self.node, self.name), args_obj)
            ok = not (isinstance(result, dict) and result.get("ok") is False)
            text = to_text(result, limit)
        except Exception as exc:  # tool errors are fed back to the agent
            ok, text = False, f"error: {type(exc).__name__}: {exc}"[:2000]
        self.emit("tool_result", f"{name}: {'ok' if ok else 'failed'} — {text[:160]}",
                  {"tool": name, "ok": ok, "seconds": round(time.monotonic() - started, 2), "output": text[:4000]})
        return text


def _short_args(args: dict[str, Any]) -> str:
    parts = []
    for k, v in args.items():
        if k == "code":
            continue
        s = json.dumps(v, default=str, ensure_ascii=False)
        parts.append(f"{k}={s[:60]}{'…' if len(s) > 60 else ''}")
    return ", ".join(parts)[:220]


def read_prompt_file(name: str) -> Path:
    return prompts_dir() / f"{name}.md"

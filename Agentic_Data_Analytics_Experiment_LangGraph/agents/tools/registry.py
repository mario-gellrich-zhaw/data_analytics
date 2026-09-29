"""Tool registry. Each agent gets a fixed whitelist of tool names (see agents/*.py)."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel


@dataclass
class ToolContext:
    ctx: Any            # ada.context.RunContext
    node: str
    agent: str


@dataclass
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    fn: Callable[[ToolContext, Any], Any]
    network: bool = False


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, params: type[BaseModel], *, network: bool = False):
    def wrap(fn: Callable[[ToolContext, Any], Any]) -> Callable[[ToolContext, Any], Any]:
        REGISTRY[name] = Tool(name, description, params, fn, network)
        return fn
    return wrap


def inline_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Pydantic JSON schema with $defs inlined (function parameters must be self-contained)."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                ref = node["$ref"].split("/")[-1]
                return resolve(copy.deepcopy(defs[ref]))
            return {k: resolve(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)


def openai_spec(t: Tool) -> dict[str, Any]:
    return {"type": "function", "name": t.name, "description": t.description,
            "parameters": inline_schema(t.params), "strict": False}


def submit_spec(model: type[BaseModel], description: str) -> dict[str, Any]:
    return {"type": "function", "name": "submit_result", "description": description,
            "parameters": inline_schema(model), "strict": False}


def to_text(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, ensure_ascii=False, indent=1)
    if len(text) > limit:
        text = text[:limit] + f"\n...[truncated {len(text) - limit} chars]"
    return text

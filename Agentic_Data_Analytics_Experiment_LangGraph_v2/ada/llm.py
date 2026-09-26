"""OpenAI access (Responses API) with budget enforcement and cost tracking.

Model names come from config.yaml (`roles` -> `models`), never from code.
"""
from __future__ import annotations

import os
import time
from typing import Any

import httpx
import openai
from openai import OpenAI

from ada.budget import BudgetTracker
from ada.config import Config
from ada.events import EventStore

_RETRYABLE = (openai.RateLimitError, openai.APIConnectionError, openai.APITimeoutError,
              openai.InternalServerError)


def _is_reasoning_model(model: str) -> bool:
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


class LLM:
    def __init__(self, cfg: Config, budget: BudgetTracker, events: EventStore, run_id: str,
                 client: Any | None = None):
        self.cfg = cfg
        self.budget = budget
        self.events = events
        self.run_id = run_id
        self._client = client

    @property
    def client(self) -> Any:
        if self._client is None:
            if not os.environ.get("OPENAI_API_KEY"):
                raise RuntimeError("OPENAI_API_KEY is not set (expected in .env)")
            self._client = OpenAI(timeout=httpx.Timeout(600.0, connect=20.0), max_retries=0)
        return self._client

    def create(self, *, role: str, agent: str, node: str | None, instructions: str,
               input: list[Any] | str, tools: list[dict[str, Any]] | None = None,
               tool_choice: Any = "auto", use_reserve: bool = False,
               max_output_tokens: int | None = None) -> Any:
        self.budget.check(use_reserve=use_reserve)
        model = self.cfg.model_for(role)
        kwargs: dict[str, Any] = {"model": model, "instructions": instructions, "input": input, "store": False}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
            kwargs["parallel_tool_calls"] = True
        if _is_reasoning_model(model):
            effort = self.cfg.reasoning_effort_for(role)
            if effort:
                kwargs["reasoning"] = {"effort": effort}
            kwargs["include"] = ["reasoning.encrypted_content"]
        if max_output_tokens:
            kwargs["max_output_tokens"] = max_output_tokens
        resp = self._call(kwargs)
        self._account(resp, model=model, agent=agent, node=node)
        return resp

    def _call(self, kwargs: dict[str, Any]) -> Any:
        delay = 2.0
        for attempt in range(5):
            try:
                return self.client.responses.create(**kwargs)
            except _RETRYABLE:
                if attempt == 4:
                    raise
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")

    def _account(self, resp: Any, *, model: str, agent: str, node: str | None) -> None:
        usage = getattr(resp, "usage", None)
        inp = int(getattr(usage, "input_tokens", 0) or 0)
        out = int(getattr(usage, "output_tokens", 0) or 0)
        details = getattr(usage, "input_tokens_details", None)
        cached = int(getattr(details, "cached_tokens", 0) or 0) if details else 0
        searches = sum(1 for item in (getattr(resp, "output", None) or [])
                       if getattr(item, "type", "") == "web_search_call")
        usd = self.budget.record(agent=agent, model=model, input_tokens=inp, cached_tokens=cached,
                                 output_tokens=out, web_searches=searches)
        snap = self.budget.snapshot()
        self.events.emit(self.run_id, "budget",
                         f"{agent}: {inp + out} tokens, ${usd:.4f} (total ${snap['usd']:.3f})",
                         node=node, agent=agent,
                         payload={"call": {"model": model, "input_tokens": inp, "cached_tokens": cached,
                                           "output_tokens": out, "usd": usd, "web_searches": searches},
                                  "totals": snap})

    # ------------------------------------------------------------------
    def web_search(self, query: str, *, agent: str, node: str | None) -> dict[str, Any]:
        provider = self.cfg.get("web.search_provider", "openai")
        if provider == "tavily" and os.environ.get("TAVILY_API_KEY"):
            return self._tavily(query)
        resp = self.create(
            role="web_search", agent=agent, node=node,
            instructions=("You are a research assistant. Search the web and answer concisely. For every "
                          "candidate data source give: name, direct URL (prefer direct download/API URLs), "
                          "format, approximate size/rows, license/terms if stated, and whether a login is required."),
            input=query, tools=[{"type": "web_search"}], tool_choice="auto")
        sources: list[dict[str, str]] = []
        for item in resp.output or []:
            if getattr(item, "type", "") != "message":
                continue
            for part in getattr(item, "content", []) or []:
                for ann in getattr(part, "annotations", []) or []:
                    url = getattr(ann, "url", None)
                    if url and all(s["url"] != url for s in sources):
                        sources.append({"url": url, "title": getattr(ann, "title", "") or ""})
        return {"answer": resp.output_text, "sources": sources[:15]}

    def _tavily(self, query: str) -> dict[str, Any]:
        resp = httpx.post("https://api.tavily.com/search",
                          json={"api_key": os.environ["TAVILY_API_KEY"], "query": query, "max_results": 8,
                                "include_answer": True}, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        self.budget.add_usd(float(self.cfg.get("pricing_web_search_call_usd", 0.01)), agent="web_search")
        return {"answer": data.get("answer", ""),
                "sources": [{"url": r["url"], "title": r.get("title", ""), "snippet": r.get("content", "")[:300]}
                            for r in data.get("results", [])]}

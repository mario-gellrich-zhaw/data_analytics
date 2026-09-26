"""Configuration: `config.yaml` + per-run overrides + environment.

The OpenAI key is never stored here. It is read from `.env` (project dir or any
parent — in this repo it lives in the repository root) into the backend's
process environment only. The sandbox never receives it.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config.yaml"


def load_env() -> Path | None:
    """Load the nearest `.env` walking up from the project dir. Never overrides
    variables that are already set (e.g. by docker compose `env_file`)."""
    for directory in [PROJECT_ROOT, *PROJECT_ROOT.parents]:
        candidate = directory / ".env"
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            return candidate
    return None


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


class Config:
    """Thin wrapper over the parsed YAML with dotted-key access."""

    def __init__(self, data: dict[str, Any]):
        self.data = data

    @classmethod
    def load(cls, overrides: dict[str, Any] | None = None, path: Path | None = None) -> "Config":
        with open(path or CONFIG_PATH, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        data = deep_merge(data, overrides or {})
        env_ha = os.environ.get("HUMAN_APPROVAL")
        if env_ha is not None and "human_approval" not in (overrides or {}):
            data["human_approval"] = env_ha.strip().lower() in {"1", "true", "yes"}
        if os.environ.get("ADA_SANDBOX_MODE"):
            data.setdefault("sandbox", {})["mode"] = os.environ["ADA_SANDBOX_MODE"]
        return cls(data)

    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # -- models -----------------------------------------------------------
    def model_for(self, role: str) -> str:
        tier = self.get(f"roles.{role}", "worker")
        return self.get(f"models.{tier}")

    def reasoning_effort_for(self, role: str) -> str | None:
        tier = self.get(f"roles.{role}", "worker")
        return self.get(f"models.reasoning_effort.{tier}")

    def price(self, model: str) -> tuple[float, float, float]:
        """USD per 1M tokens: (input, cached_input, output)."""
        table = self.get("pricing_usd_per_1m", {})
        for name in sorted(table, key=len, reverse=True):  # longest prefix wins
            if model.startswith(name):
                p = table[name]
                return float(p[0]), float(p[1]), float(p[2])
        return (2.0, 0.5, 10.0)  # conservative fallback for unknown models

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.data)

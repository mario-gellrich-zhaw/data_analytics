"""Budget tracking: USD, tokens, wall time. Every LLM call goes through
`BudgetTracker.check()` before and `record()` after, so a run can never spend
past its limits by more than one call."""
from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from ada.config import Config


class BudgetExceeded(RuntimeError):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass
class BudgetUsage:
    usd: float = 0.0
    input_tokens: int = 0
    cached_tokens: int = 0
    output_tokens: int = 0
    llm_calls: int = 0
    web_searches: int = 0
    elapsed_seconds: float = 0.0          # accumulated active time across resumes
    by_agent: dict[str, dict[str, float]] = field(default_factory=dict)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class BudgetTracker:
    def __init__(self, cfg: Config, snapshot: dict[str, Any] | None = None):
        self.cfg = cfg
        self.limits = {
            "max_usd": float(cfg.get("budgets.max_usd", 5.0)),
            "max_tokens": int(cfg.get("budgets.max_tokens", 5_000_000)),
            "max_wall_seconds": float(cfg.get("budgets.max_wall_seconds", 3600)),
        }
        self.reserve_usd = float(cfg.get("budgets.presenter_reserve_usd", 0.0))
        self.usage = BudgetUsage()
        if snapshot:
            known = {k: v for k, v in snapshot.items() if k in BudgetUsage.__dataclass_fields__}
            self.usage = BudgetUsage(**known)
        self._segment_start = time.monotonic()
        self._base_elapsed = self.usage.elapsed_seconds
        self._lock = threading.Lock()

    # -- time --------------------------------------------------------------
    def elapsed(self) -> float:
        return self._base_elapsed + (time.monotonic() - self._segment_start)

    # -- checks ------------------------------------------------------------
    def exhausted_reason(self, *, use_reserve: bool = False) -> str | None:
        usd_limit = self.limits["max_usd"] - (0.0 if use_reserve else self.reserve_usd)
        if self.usage.usd >= usd_limit:
            return f"USD budget exhausted ({self.usage.usd:.3f} of {self.limits['max_usd']:.2f})"
        if self.usage.total_tokens >= self.limits["max_tokens"]:
            return f"token budget exhausted ({self.usage.total_tokens} of {self.limits['max_tokens']})"
        if self.elapsed() >= self.limits["max_wall_seconds"]:
            return f"wall-time budget exhausted ({self.elapsed():.0f}s of {self.limits['max_wall_seconds']:.0f}s)"
        return None

    def check(self, *, use_reserve: bool = False) -> None:
        reason = self.exhausted_reason(use_reserve=use_reserve)
        if reason:
            raise BudgetExceeded(reason)

    # -- accounting ----------------------------------------------------------
    def cost_of(self, model: str, input_tokens: int, cached_tokens: int, output_tokens: int) -> float:
        p_in, p_cached, p_out = self.cfg.price(model)
        uncached = max(0, input_tokens - cached_tokens)
        return (uncached * p_in + cached_tokens * p_cached + output_tokens * p_out) / 1_000_000

    def record(self, *, agent: str, model: str, input_tokens: int, cached_tokens: int,
               output_tokens: int, web_searches: int = 0) -> float:
        usd = self.cost_of(model, input_tokens, cached_tokens, output_tokens)
        usd += web_searches * float(self.cfg.get("pricing_web_search_call_usd", 0.01))
        with self._lock:
            u = self.usage
            u.usd += usd
            u.input_tokens += input_tokens
            u.cached_tokens += cached_tokens
            u.output_tokens += output_tokens
            u.llm_calls += 1
            u.web_searches += web_searches
            per = u.by_agent.setdefault(agent, {"usd": 0.0, "tokens": 0, "calls": 0})
            per["usd"] += usd
            per["tokens"] += input_tokens + output_tokens
            per["calls"] += 1
        return usd

    def add_usd(self, usd: float, agent: str = "system") -> None:
        """Test hook / non-LLM costs."""
        with self._lock:
            self.usage.usd += usd
            per = self.usage.by_agent.setdefault(agent, {"usd": 0.0, "tokens": 0, "calls": 0})
            per["usd"] += usd

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self.usage.elapsed_seconds = self.elapsed()
            data = asdict(self.usage)
        data["total_tokens"] = self.usage.total_tokens
        data["limits"] = dict(self.limits)
        return data

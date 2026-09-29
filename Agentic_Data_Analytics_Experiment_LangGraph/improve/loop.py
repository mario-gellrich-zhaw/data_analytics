"""One self-improvement cycle (`make improve`). Protected code.

1. Benchmark the current version (HEAD) in a clean git worktree.
2. Summarise traces; the ImproverAgent proposes <= 3 changes restricted to the
   editable surface (improve/allowlist.py, enforced here in code).
3. Apply them on a new branch `improve/<id>` in a second worktree, run the unit
   tests there, re-run the benchmark.
4. Accept only if the mean score gain exceeds max(min_improvement, 2 x standard
   error) and all tests pass -> merge the branch; otherwise delete it.
5. Append the full record to improvements.jsonl (shown in the UI).
"""
from __future__ import annotations

import argparse
import difflib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ada.budget import BudgetTracker
from ada.config import PROJECT_ROOT, Config, load_env
from ada.context import RunContext
from ada.events import default_store
from ada.paths import improvements_log
from ada.schemas import ImprovementProposal
from ada.store import RunStore
from ada.vault import HoldoutVault
from agents.base import Agent
from agents.tools.registry import ToolContext, tool
from improve.allowlist import EDITABLE_GLOBS, ChangeRejected, apply_change
from improve.benchmark import run_suite


# ---------------------------------------------------------------------------- git helpers
def git(*args: str, cwd: Path) -> str:
    res = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {res.stderr.strip()}")
    return res.stdout.strip()


def repo_root() -> Path:
    return Path(git("rev-parse", "--show-toplevel", cwd=PROJECT_ROOT))


def project_rel() -> str:
    return str(PROJECT_ROOT.relative_to(repo_root()))


def add_worktree(path: Path, ref: str, branch: str | None = None) -> Path:
    args = ["worktree", "add", "--quiet"]
    args += ["-b", branch, str(path), ref] if branch else ["--detach", str(path), ref]
    git(*args, cwd=repo_root())
    os.chmod(path, 0o755)
    return path / project_rel()


def remove_worktree(path: Path) -> None:
    try:
        git("worktree", "remove", "--force", str(path), cwd=repo_root())
    except RuntimeError:
        shutil.rmtree(path, ignore_errors=True)
        git("worktree", "prune", cwd=repo_root())


# ---------------------------------------------------------------------------- improver agent
class ReadArgs(BaseModel):
    path: str = Field(description="project-relative path, e.g. prompts/modeling.md or agents/tools/web.py")
    max_chars: int = Field(12000, ge=500, le=40000)


_IMPROVE_BASE: dict[str, Path] = {}


@tool("read_project_file", "Read a file of the system under improvement (prompts, config, tools, agents, ada code).",
      ReadArgs)
def read_project_file(tc: ToolContext, a: ReadArgs) -> Any:
    base = _IMPROVE_BASE["dir"]
    target = (base / a.path).resolve()
    if base not in target.parents or any(p in target.parts for p in (".env", ".venv", "var", "node_modules")):
        return "refused"
    if not target.is_file():
        return "not found"
    return target.read_text(encoding="utf-8", errors="replace")[:a.max_chars]


class ListArgs(BaseModel):
    pass


@tool("list_editable_files", "List the files the improver may change.", ListArgs)
def list_editable_files(tc: ToolContext, a: ListArgs) -> Any:
    base = _IMPROVE_BASE["dir"]
    files = ["config.yaml"]
    for g in EDITABLE_GLOBS:
        files += sorted(str(p.relative_to(base)) for p in base.glob(g))
    return files


class ImproverAgent(Agent):
    name = "ImproverAgent"
    role = "improver"
    prompt = "improver"
    tools = ["read_project_file", "list_editable_files"]
    output_model = ImprovementProposal

    def max_steps(self) -> int:
        return 18


# ---------------------------------------------------------------------------- traces
def summarise_traces(suite: dict[str, Any]) -> list[dict[str, Any]]:
    store = default_store()
    out = []
    for run in suite["runs"]:
        rid = run["run_id"]
        events = store.events(rid, limit=20000)
        gates = [e["payload"] for e in events if e["type"] == "gate" and e["payload"].get("decision")]
        failures = []
        for g in gates:
            if g["decision"] != "pass":
                failures.append({"node": g["node"], "decision": g["decision"],
                                 "failed_checks": [f"{c['name']}: {c['detail'][:160]}" for c in g.get("checks", [])
                                                   if not c["passed"]][:5],
                                 "critic": ((g.get("critique") or {}).get("issues") or [])[:3]})
        tool_fail: dict[str, int] = {}
        for e in events:
            if e["type"] == "tool_result" and e["payload"].get("ok") is False:
                key = f"{e['agent']}:{e['payload'].get('tool')}"
                tool_fail[key] = tool_fail.get(key, 0) + 1
        budget = next((e["payload"]["totals"] for e in reversed(events) if e["type"] == "budget" and "totals" in e["payload"]), {})
        out.append({
            "run_id": rid, "task": run["task"], "status": run["record"]["status"], "score": run["score"],
            "visits": (run["record"]["summary"] or {}).get("visits"),
            "gate_failures": failures[:12], "failed_tool_calls": tool_fail,
            "errors": [e["summary"][:200] for e in events if e["type"] == "error"][:6],
            "usd_by_agent": {k: round(v.get("usd", 0), 4) for k, v in (budget.get("by_agent") or {}).items()},
        })
    return out


def _ctx(cycle_id: str) -> RunContext:
    cfg = Config.load({"budgets": {"max_usd": 2.0, "presenter_reserve_usd": 0}})
    run_id = f"improve-{cycle_id}"
    events = default_store()
    events.create_run(run_id, "self-improvement cycle", None, "improve", {}, cycle_id)
    return RunContext(run_id=run_id, cfg=cfg, events=events, budget=BudgetTracker(cfg),
                      store=RunStore(run_id).init(), vault=HoldoutVault(run_id, cfg))


def propose(ctx: RunContext, base_dir: Path, baseline: dict[str, Any], history: list[dict[str, Any]]) -> tuple[dict, list]:
    _IMPROVE_BASE["dir"] = base_dir
    task = (
        "Benchmark results of the current system version (scores in [0,1]; components metric/gates/cost/time):\n"
        f"mean {baseline['mean']} ± {baseline['stdev']}; per task {json.dumps(baseline['per_task'])}\n\n"
        f"Run traces:\n{json.dumps(summarise_traces(baseline), indent=1, default=str)[:14000]}\n\n"
        f"Earlier improvement attempts (most recent first):\n{json.dumps(history[:5], default=str)[:3000]}\n\n"
        "Propose at most 3 targeted changes."
    )
    agent = ImproverAgent(ctx, "improve")
    read = lambda p: (base_dir / p).read_text() if (base_dir / p).is_file() else None  # noqa: E731
    feedback = ""
    for attempt in range(3):
        proposal = agent.run(task + feedback).model_dump()
        try:
            changes = [apply_change(c, read) for c in proposal["changes"][:3]]
            if not changes:
                raise ChangeRejected("no changes proposed")
            return proposal, changes
        except ChangeRejected as exc:
            ctx.emit("improvement", f"proposal rejected by allowlist: {exc}", agent="ImproverAgent",
                     payload={"kind": "allowlist_rejection", "error": str(exc)})
            feedback = f"\n\nYour previous proposal was REJECTED by the allowlist: {exc}. Stay inside the editable surface."
    raise ChangeRejected("no valid proposal after 3 attempts")


# ---------------------------------------------------------------------------- the cycle
def run_tests(project_dir: Path) -> tuple[bool, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("ADA_")}
    res = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x"], cwd=project_dir, env=env,
                         capture_output=True, text=True, timeout=1800)
    return res.returncode == 0, (res.stdout + res.stderr)[-3000:]


def decide(baseline: dict[str, Any], candidate: dict[str, Any], min_gain: float) -> tuple[bool, float, float]:
    b = [r["score"]["total"] for r in baseline["runs"]]
    c = [r["score"]["total"] for r in candidate["runs"]]
    delta = statistics.fmean(c) - statistics.fmean(b)
    var_b = statistics.variance(b) if len(b) > 1 else 0.0
    var_c = statistics.variance(c) if len(c) > 1 else 0.0
    se = math.sqrt(var_b / len(b) + var_c / len(c))
    margin = max(min_gain, 2 * se)
    return delta > margin, round(delta, 4), round(margin, 4)


def load_history() -> list[dict[str, Any]]:
    path = improvements_log()
    if not path.exists():
        return []
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    return [{k: r.get(k) for k in ("id", "accepted", "reason", "delta")} | {"diagnosis": (r.get("proposal") or {}).get("diagnosis")}
            for r in reversed(rows)]


def cycle(tasks: list[str] | None, repeats: int | None, keep: bool = False) -> dict[str, Any]:
    load_env()
    cfg = Config.load()
    rel = project_rel()
    dirty = git("status", "--porcelain", "--", ".", ":!improvements.jsonl", cwd=PROJECT_ROOT)
    if dirty:
        raise SystemExit(f"commit or stash changes in {rel} first — the benchmark must run a committed version:\n{dirty}")
    base_sha = git("rev-parse", "--short", "HEAD", cwd=PROJECT_ROOT)
    current_branch = git("rev-parse", "--abbrev-ref", "HEAD", cwd=PROJECT_ROOT)
    cycle_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    branch = f"improve/{cycle_id}"
    tmp = Path(tempfile.gettempdir()) / f"ada-improve-{cycle_id}"
    tmp.mkdir(mode=0o755)
    os.chmod(tmp, 0o755)
    ctx = _ctx(cycle_id)
    record: dict[str, Any] = {"id": cycle_id, "ts": time.time(), "base_version": base_sha, "branch": branch,
                              "accepted": False, "status": "running"}

    def note(msg: str, **payload: Any) -> None:
        print(f"[improve {cycle_id}] {msg}", flush=True)
        ctx.emit("improvement", msg, agent="ImproverAgent", payload=payload)

    base_dir = cand_dir = None
    try:
        base_dir = add_worktree(tmp / "base", base_sha)
        note(f"benchmarking base {base_sha} in {base_dir}")
        baseline = run_suite(base_dir, tasks=tasks, repeats=repeats, label=f"{cycle_id[-4:]}b")
        record["baseline"] = {k: baseline[k] for k in ("mean", "stdev", "per_task")}
        note(f"baseline mean {baseline['mean']} ± {baseline['stdev']}", baseline=record["baseline"])

        proposal, changes = propose(ctx, base_dir, baseline, load_history())
        record["proposal"] = proposal
        diff = "".join(difflib.unified_diff((old or "").splitlines(True), new.splitlines(True), f"a/{p}", f"b/{p}")
                       for p, old, new in changes)
        record["diff"] = diff[:40000]
        note(f"proposal: {proposal['diagnosis'][:200]}", files=[p for p, _, _ in changes])

        cand_dir = add_worktree(tmp / "cand", base_sha, branch=branch)
        for p, _, new in changes:
            (cand_dir / p).parent.mkdir(parents=True, exist_ok=True)
            (cand_dir / p).write_text(new)
        git("add", "--", *[p for p, _, _ in changes], cwd=cand_dir)
        git("commit", "-q", "-m", f"improve {cycle_id}: {proposal['diagnosis'][:60]}\n\n{proposal['expected_effect'][:500]}\n\n"
            "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>", cwd=cand_dir)

        tests_ok, test_log = run_tests(cand_dir)
        record["tests_passed"] = tests_ok
        record["test_log_tail"] = test_log[-1500:]
        note(f"unit tests on candidate: {'passed' if tests_ok else 'FAILED'}")
        if not tests_ok:
            record["reason"] = "rejected: unit tests fail on the candidate"
        else:
            candidate = run_suite(cand_dir, tasks=tasks, repeats=repeats, label=f"{cycle_id[-4:]}c")
            record["candidate"] = {k: candidate[k] for k in ("mean", "stdev", "per_task")}
            accepted, delta, margin = decide(baseline, candidate, float(cfg.get("improve.min_improvement", 0.02)))
            record.update({"delta": delta, "margin": margin, "accepted": accepted})
            record["reason"] = (f"{'accepted' if accepted else 'rejected'}: mean {baseline['mean']} -> {candidate['mean']} "
                                f"(delta {delta:+.4f}, required > {margin:.4f})")
            note(record["reason"], delta=delta, margin=margin)
            if accepted:
                git("merge", "--no-ff", "--no-edit", branch, cwd=PROJECT_ROOT)
                record["merged_into"] = current_branch
        record["status"] = "done"
    except ChangeRejected as exc:
        record.update(status="error", reason=f"no valid proposal: {exc}")
    except Exception as exc:
        record.update(status="error", reason=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        record["improver_usd"] = round(ctx.budget.snapshot()["usd"], 4)
        with open(improvements_log(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")
        ctx.events.update_run(ctx.run_id, status="completed" if record["status"] == "done" else "failed",
                              summary={k: record.get(k) for k in ("accepted", "reason", "delta")})
        if not keep:
            for wt in (tmp / "base", tmp / "cand"):
                if wt.exists():
                    remove_worktree(wt)
            shutil.rmtree(tmp, ignore_errors=True)
        if not record.get("accepted"):
            try:
                git("branch", "-D", branch, cwd=PROJECT_ROOT)
            except RuntimeError:
                pass
    return record


def main() -> int:
    ap = argparse.ArgumentParser(description="run one self-improvement cycle")
    ap.add_argument("--tasks", nargs="*")
    ap.add_argument("--repeats", type=int)
    ap.add_argument("--keep-worktrees", action="store_true")
    a = ap.parse_args()
    rec = cycle(a.tasks, a.repeats, keep=a.keep_worktrees)
    print(json.dumps({k: rec.get(k) for k in ("id", "status", "accepted", "reason", "baseline", "candidate")}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())

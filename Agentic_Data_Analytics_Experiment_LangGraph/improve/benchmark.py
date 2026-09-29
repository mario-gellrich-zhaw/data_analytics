"""Benchmark suite runner (protected).

    python -m improve.benchmark [--tasks diabetes_regression synthetic_rentals] [--repeats 2]

Each task run is a separate `python -m ada.cli run --task ...` process started in
`project_dir` (the current checkout or an improvement worktree). All runs write to
the same var dir, so they appear in the UI.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import yaml

from ada.config import PROJECT_ROOT, Config
from ada.events import default_store
from ada.paths import var_dir
from improve.scoring import score_run

TASKS_FILE = PROJECT_ROOT / "benchmarks" / "tasks.yaml"


def load_tasks() -> dict[str, dict[str, Any]]:
    return yaml.safe_load(TASKS_FILE.read_text())["tasks"]


def _run_one(project_dir: Path, task_id: str, label: str, repeat: int, limits: dict[str, float]) -> dict[str, Any]:
    run_id = f"bench-{label}-{task_id}-{repeat}-{uuid.uuid4().hex[:4]}"
    out = var_dir() / "bench" / f"{run_id}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, "-m", "ada.cli", "run", "--task", task_id, "--run-id", run_id, "--json-out", str(out),
           "--max-usd", str(limits["max_usd"]), "--max-minutes", str(limits["max_wall_seconds"] / 60)]
    env = {**os.environ, "ADA_VAR_DIR": str(var_dir()), "PYTHONPATH": str(project_dir)}
    started = time.time()
    log = var_dir() / "bench" / f"{run_id}.log"
    with open(log, "w") as fh:
        proc = subprocess.run(cmd, cwd=project_dir, env=env, stdout=fh, stderr=subprocess.STDOUT,
                              timeout=limits["max_wall_seconds"] + 900)
    record = default_store().get_run(run_id) or {"status": "failed", "summary": {}}
    return {"run_id": run_id, "task": task_id, "repeat": repeat, "returncode": proc.returncode,
            "seconds": round(time.time() - started, 1), "record": record, "log": str(log)}


def run_suite(project_dir: Path, *, tasks: list[str] | None = None, repeats: int | None = None,
              label: str = "base", parallel: int | None = None) -> dict[str, Any]:
    cfg = Config.load()
    all_tasks = load_tasks()
    tasks = tasks or list(cfg.get("improve.tasks"))
    repeats = int(repeats or cfg.get("improve.repeats", 2))
    weights = cfg.get("improve.weights")
    over = (cfg.get("improve.budget_overrides") or {}).get("budgets", {})
    limits = {"max_usd": float(over.get("max_usd", cfg.get("budgets.max_usd"))),
              "max_wall_seconds": float(over.get("max_wall_seconds", cfg.get("budgets.max_wall_seconds")))}
    jobs = [(t, r) for t in tasks for r in range(repeats)]
    with ThreadPoolExecutor(max_workers=int(parallel or cfg.get("improve.parallel", 4))) as pool:
        results = list(pool.map(lambda j: _run_one(project_dir, j[0], label, j[1], limits), jobs))
    store = default_store()
    per_task: dict[str, list[float]] = {}
    for res in results:
        gates = store.events(res["run_id"], types=["gate"])
        res["score"] = score_run(res["record"], gates, all_tasks[res["task"]], weights, limits)
        per_task.setdefault(res["task"], []).append(res["score"]["total"])
        res["record"] = {k: res["record"].get(k) for k in ("id", "status", "summary")}
    totals = [r["score"]["total"] for r in results]
    return {
        "label": label, "project_dir": str(project_dir), "tasks": tasks, "repeats": repeats, "limits": limits,
        "mean": round(statistics.fmean(totals), 4) if totals else 0.0,
        "stdev": round(statistics.stdev(totals), 4) if len(totals) > 1 else 0.0,
        "per_task": per_task, "runs": results,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tasks", nargs="*")
    ap.add_argument("--repeats", type=int)
    ap.add_argument("--parallel", type=int)
    a = ap.parse_args()
    res = run_suite(PROJECT_ROOT, tasks=a.tasks, repeats=a.repeats, parallel=a.parallel, label="manual")
    print(json.dumps({k: res[k] for k in ("mean", "stdev", "per_task")}, indent=1))
    for r in res["runs"]:
        print(r["run_id"], r["record"]["status"], r["score"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

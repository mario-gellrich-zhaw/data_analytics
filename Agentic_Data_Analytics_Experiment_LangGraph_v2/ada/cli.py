"""Command line entry points.

    python -m ada.cli run --objective "Build a price prediction model for rental apartments." [--region Zurich]
    python -m ada.cli run --stub                       # scripted demo run, no LLM
    python -m ada.cli run --task diabetes_regression   # a benchmark task (offline)
    python -m ada.cli resume <run_id>
    python -m ada.cli list
"""
from __future__ import annotations

import argparse
import json
import sys

from ada.runner import RunManager


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="ada")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--objective", default="Build a price prediction model for rental apartments.")
    r.add_argument("--region", default=None)
    r.add_argument("--stub", action="store_true", help="scripted demo run without LLM calls")
    r.add_argument("--offline", action="store_true", help="no web access; local datasets only")
    r.add_argument("--task", default=None, help="benchmark task id from benchmarks/tasks.yaml")
    r.add_argument("--max-usd", type=float, default=None)
    r.add_argument("--max-minutes", type=float, default=None)
    r.add_argument("--run-id", default=None)
    r.add_argument("--json-out", default=None, help="write the final run record here")
    s = sub.add_parser("resume")
    s.add_argument("run_id")
    sub.add_parser("list")
    a = ap.parse_args(argv)

    mgr = RunManager()
    if a.cmd == "list":
        for run in mgr.events.list_runs():
            print(f"{run['id']}  {run['status']:<18} {run['mode']:<5} {run['objective'][:70]}")
        return 0
    if a.cmd == "resume":
        mgr.resume(a.run_id, background=False)
        print(json.dumps(mgr.events.get_run(a.run_id), indent=1, default=str))
        return 0

    objective, region = a.objective, a.region
    options: dict = {"mode": "stub" if a.stub else "real", "offline": a.offline}
    if a.task:
        from improve.benchmark import load_tasks
        task = load_tasks()[a.task]
        objective, region = task["objective"], task.get("region")
        options.update({"offline": bool(task.get("offline", False)), "task_id": a.task})
    budgets = {}
    if a.max_usd is not None:
        budgets["max_usd"] = a.max_usd
    if a.max_minutes is not None:
        budgets["max_wall_seconds"] = a.max_minutes * 60
    if budgets:
        options["budgets"] = budgets
    run_id = mgr.start(objective, region, options, background=False, run_id=a.run_id)
    record = mgr.events.get_run(run_id)
    print(json.dumps({"run_id": run_id, "status": record["status"], "summary": record["summary"]}, indent=1, default=str))
    if a.json_out:
        with open(a.json_out, "w") as fh:
            json.dump(record, fh, default=str)
    return 0 if record["status"] in ("completed", "budget_exhausted") else 1


if __name__ == "__main__":
    sys.exit(main())

"""Sandbox service for docker compose: runs inside its own container that mounts
only the runs volume (read-write) and the harness (read-only) — never the vault
or `.env` — and sits on an internal network without internet egress.

    uvicorn ada.sandbox.runner_service:app --host 0.0.0.0 --port 8100
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

RUNS_DIR = Path(os.environ.get("ADA_RUNS_DIR", "/runs")).resolve()
HARNESS_DIR = Path(__file__).resolve().parent.parent / "evaluation"
KIT_DIR = Path(__file__).resolve().parent / "kit"

app = FastAPI(title="ADA sandbox")


class RunRequest(BaseModel):
    run_dir: str
    script: str
    timeout: int = 300
    args: list[str] = []


@app.post("/run")
def run(req: RunRequest) -> dict:
    workspace = (RUNS_DIR / req.run_dir).resolve()
    if RUNS_DIR not in workspace.parents:
        raise HTTPException(400, "bad run_dir")
    if req.script.startswith("harness:"):
        script = (HARNESS_DIR / req.script.split(":", 1)[1]).resolve()
        if HARNESS_DIR not in script.parents:
            raise HTTPException(400, "bad harness script")
    else:
        script = (workspace / req.script).resolve()
        if workspace not in script.parents:
            raise HTTPException(400, "bad script")
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": str(workspace / ".home"), "MPLBACKEND": "Agg",
        "PYTHONPATH": str(KIT_DIR), "PYTHONUNBUFFERED": "1", "ADA_WORKSPACE": str(workspace),
        "ADA_ALLOW_NETWORK": "0", "ADA_MEMORY_MB": os.environ.get("ADA_MEMORY_MB", "6144"),
        "ADA_CPU_SECONDS": os.environ.get("ADA_CPU_SECONDS", "900"),
    }
    try:
        proc = subprocess.run(["timeout", "-s", "KILL", str(req.timeout), sys.executable, str(script), *req.args],
                              cwd=workspace, env=env, capture_output=True, text=True, errors="replace",
                              timeout=req.timeout + 30)
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr,
                "timed_out": proc.returncode in (124, 137)}
    except subprocess.TimeoutExpired:
        return {"returncode": -9, "stdout": "", "stderr": "timeout", "timed_out": True}


@app.get("/health")
def health() -> dict:
    return {"ok": True}

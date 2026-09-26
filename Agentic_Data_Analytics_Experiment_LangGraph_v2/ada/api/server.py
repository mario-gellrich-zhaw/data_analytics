"""FastAPI backend: REST for runs/artifacts/improvements + WebSocket event stream.

    uvicorn ada.api.server:app --port 8000
"""
from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ada.config import PROJECT_ROOT, Config
from ada.graph.topology import graph_spec
from ada.paths import improvements_log
from ada.runner import RunManager
from ada.store import PathDenied, RunStore

app = FastAPI(title="Autonomous data analytics agents")
manager = RunManager()
interrupted = manager.events.mark_orphans_interrupted()


class StartRequest(BaseModel):
    objective: str = Field(min_length=5)
    region: Optional[str] = None
    mode: str = Field("real", pattern="^(real|stub)$")
    offline: bool = False
    max_usd: Optional[float] = Field(None, gt=0, le=100)
    max_minutes: Optional[float] = Field(None, gt=0, le=600)
    max_loopbacks: Optional[int] = Field(None, ge=0, le=50)
    human_approval: bool = False
    stub_delay: Optional[float] = Field(None, ge=0, le=5)


class ApproveRequest(BaseModel):
    next_node: Optional[str] = None
    note: str = ""


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "interrupted_at_startup": interrupted}


@app.get("/api/graph")
def graph() -> dict[str, Any]:
    return graph_spec()


@app.get("/api/config")
def config() -> dict[str, Any]:
    cfg = Config.load()
    return {"budgets": cfg.get("budgets"), "models": cfg.get("models"), "roles": cfg.get("roles"),
            "human_approval": cfg.get("human_approval")}


@app.get("/api/runs")
def list_runs() -> list[dict[str, Any]]:
    runs = manager.events.list_runs(200)
    for r in runs:
        r["active"] = manager.is_active(r["id"])
    return runs


@app.post("/api/runs")
def start_run(req: StartRequest) -> dict[str, Any]:
    budgets: dict[str, Any] = {}
    if req.max_usd is not None:
        budgets["max_usd"] = req.max_usd
    if req.max_minutes is not None:
        budgets["max_wall_seconds"] = req.max_minutes * 60
    if req.max_loopbacks is not None:
        budgets["max_total_loopbacks"] = req.max_loopbacks
    options: dict[str, Any] = {"mode": req.mode, "offline": req.offline, "budgets": budgets,
                               "human_approval": req.human_approval}
    if req.mode == "stub":
        options["stub_delay"] = 0.35 if req.stub_delay is None else req.stub_delay
    run_id = manager.start(req.objective, req.region, options)
    return {"run_id": run_id}


def _run_or_404(run_id: str) -> dict[str, Any]:
    run = manager.events.get_run(run_id)
    if run is None:
        raise HTTPException(404, "run not found")
    run["active"] = manager.is_active(run_id)
    return run


@app.get("/api/runs/{run_id}")
def get_run(run_id: str) -> dict[str, Any]:
    run = _run_or_404(run_id)
    if run["status"] == "awaiting_approval":
        run["interrupt"] = manager.pending_interrupt(run_id)
    return run


@app.post("/api/runs/{run_id}/resume")
def resume_run(run_id: str) -> dict[str, Any]:
    run = _run_or_404(run_id)
    if run["active"]:
        raise HTTPException(409, "run is active")
    if run["status"] in ("completed",):
        raise HTTPException(409, "run already completed")
    manager.resume(run_id)
    return {"run_id": run_id, "status": "running"}


@app.post("/api/runs/{run_id}/approve")
def approve_run(run_id: str, req: ApproveRequest) -> dict[str, Any]:
    run = _run_or_404(run_id)
    if run["status"] != "awaiting_approval":
        raise HTTPException(409, "run is not waiting for approval")
    manager.approve(run_id, req.next_node, req.note)
    return {"run_id": run_id, "status": "running"}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str) -> dict[str, Any]:
    _run_or_404(run_id)
    return {"stopping": manager.stop(run_id)}


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str, after: int = 0, limit: int = Query(2000, le=10000)) -> list[dict[str, Any]]:
    _run_or_404(run_id)
    return manager.events.events(run_id, after_id=after, limit=limit)


@app.get("/api/runs/{run_id}/artifacts")
def list_artifacts(run_id: str) -> list[dict[str, Any]]:
    _run_or_404(run_id)
    return RunStore(run_id).list_files(limit=2000)


@app.get("/api/runs/{run_id}/file")
def get_file(run_id: str, path: str) -> FileResponse:
    _run_or_404(run_id)
    store = RunStore(run_id)
    try:
        target = store.resolve(path, must_exist=True)
    except (PathDenied, FileNotFoundError):
        raise HTTPException(404, "not found")
    if target.suffix == ".pkl":
        raise HTTPException(403, "model pickles are not served")
    media = mimetypes.guess_type(target.name)[0] or "text/plain"
    if target.suffix in (".md", ".py", ".jsonl", ".txt", ".csv"):
        media = "text/plain; charset=utf-8"
    return FileResponse(target, media_type=media)


@app.get("/api/improvements")
def improvements() -> list[dict[str, Any]]:
    path = improvements_log()
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out[::-1]


@app.websocket("/ws/runs/{run_id}")
async def ws_events(ws: WebSocket, run_id: str) -> None:
    """Streams the run's events: backlog first, then new events as they are written."""
    await ws.accept()
    last = int(ws.query_params.get("after", 0))
    last_status = None
    try:
        while True:
            batch = await asyncio.to_thread(manager.events.events, run_id, last, 500)
            if batch:
                last = batch[-1]["id"]
                await ws.send_text(json.dumps({"type": "events", "events": batch}, default=str))
                continue
            run = await asyncio.to_thread(manager.events.get_run, run_id)
            status = run["status"] if run else "unknown"
            if status != last_status:
                last_status = status
                await ws.send_text(json.dumps({"type": "status", "status": status}))
            await asyncio.sleep(0.4)
    except (WebSocketDisconnect, RuntimeError):
        return


DIST = PROJECT_ROOT / "frontend" / "dist"
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/{full_path:path}")
    def spa(full_path: str) -> Any:
        candidate = (DIST / full_path).resolve()
        if full_path and DIST in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(DIST / "index.html")
else:
    @app.get("/")
    def no_ui() -> JSONResponse:
        return JSONResponse({"message": "frontend not built — run `make frontend` (or use the Vite dev server)"})

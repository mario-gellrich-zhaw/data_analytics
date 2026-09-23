"""Tiny web backend for the agentic demo (FastAPI, not Flask).

Serves a single static page and one Server-Sent-Events endpoint that runs
a fresh `DemoRun` (see demo_run.py) in a background thread and streams its
events to the browser.

A manual Stop (POST /api/stop) ends the run cleanly at any point, and a
generous safety net (max turns / max wall-clock time, see config.py) bounds
it regardless.
"""

import queue
import threading

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import STATIC_DIR
from app.demo_run import DemoRun

load_dotenv()  # finds the .env at the repo root

app = FastAPI()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# One demo at a time (this is a single-instructor classroom tool, not a
# multi-user service) — a plain module-level flag is enough to let the
# frontend's Stop button end an in-progress run.
stop_event = threading.Event()


@app.get("/")
def index():
    """Serve the single static demo page."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Avoid noisy 404s from browsers auto-requesting /favicon.ico."""
    return Response(status_code=204)


@app.post("/api/stop")
def stop():
    """Signal an in-progress run to end cleanly at its next checkpoint."""
    stop_event.set()
    return {"stopping": True}


def _run_demo(q: "queue.Queue"):
    DemoRun(q, stop_event).run()


@app.get("/api/stream")
def stream():
    """Start a fresh demo run and stream it to the browser as SSE events."""
    stop_event.clear()
    q: "queue.Queue" = queue.Queue()
    threading.Thread(target=_run_demo, args=(q,), daemon=True).start()

    def event_generator():
        # A single turn can legitimately take a while (a slow model reply, a
        # multi-MB download). Without something sent regularly, some proxies
        # / port-forwarding layers treat the connection as idle and kill it,
        # which the browser reports as "connection lost" even though the
        # backend is still working. An SSE comment line (browsers ignore
        # lines starting with ':') sent whenever nothing real has happened
        # in heartbeat_seconds keeps the connection visibly alive.
        heartbeat_seconds = 15
        while True:
            try:
                item = q.get(timeout=heartbeat_seconds)
            except queue.Empty:
                yield ": ping\n\n"
                continue
            if item is None:
                break
            yield item

    return StreamingResponse(event_generator(), media_type="text/event-stream")

"""Event log + run registry in SQLite.

Every agent action is written here; the WebSocket endpoint tails this table, so
runs started from the CLI or by `make improve` show up in the UI as well.
Event shape: {run_id, ts, node, agent, type, summary, payload}.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from ada.paths import db_path

EVENT_TYPES = {
    "message", "tool_call", "tool_result", "gate", "transition", "artifact",
    "metric", "budget", "error", "improvement",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    objective TEXT NOT NULL,
    region TEXT,
    mode TEXT,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    options TEXT,
    system_version TEXT,
    summary TEXT,
    owner_pid INTEGER
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    ts REAL NOT NULL,
    node TEXT,
    agent TEXT,
    type TEXT NOT NULL,
    summary TEXT,
    payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
"""


def _json(value: Any) -> str:
    return json.dumps(value, default=str, ensure_ascii=False)


class EventStore:
    """Thread-safe (one connection guarded by a lock). Multiple processes may
    share the file thanks to WAL mode."""

    def __init__(self, path: Path | None = None):
        self.path = Path(path or db_path())
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(_SCHEMA)
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(runs)")}
            if "owner_pid" not in cols:
                self._conn.execute("ALTER TABLE runs ADD COLUMN owner_pid INTEGER")
            self._conn.commit()

    # -- runs ------------------------------------------------------------
    def create_run(self, run_id: str, objective: str, region: str | None, mode: str,
                   options: dict[str, Any], system_version: str) -> None:
        now = time.time()
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs (id, objective, region, mode, status, created_at, updated_at, options, system_version,"
                " summary, owner_pid) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, objective, region, mode, "running", now, now, _json(options), system_version, _json({}),
                 os.getpid()),
            )
            self._conn.commit()

    def update_run(self, run_id: str, *, status: str | None = None, summary: dict[str, Any] | None = None) -> None:
        sets, args = ["updated_at=?"], [time.time()]
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if summary is not None:
            sets.append("summary=?")
            args.append(_json(summary))
        if status == "running":
            sets.append("owner_pid=?")
            args.append(os.getpid())
        args.append(run_id)
        with self._lock:
            self._conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE id=?", args)
            self._conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return _run_row(row) if row else None

    def list_runs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [_run_row(r) for r in rows]

    def mark_orphans_interrupted(self) -> list[str]:
        """Called at server start: runs still 'running' whose owning process is gone were
        killed with it (crash / restart) and can be resumed."""
        with self._lock:
            rows = self._conn.execute("SELECT id, owner_pid FROM runs WHERE status IN ('running','queued')").fetchall()
            dead = [r["id"] for r in rows if not _alive(r["owner_pid"])]
            for run_id in dead:
                self._conn.execute("UPDATE runs SET status='interrupted' WHERE id=?", (run_id,))
            self._conn.commit()
        return dead

    # -- events ----------------------------------------------------------
    def emit(self, run_id: str, type: str, summary: str, *, node: str | None = None,
             agent: str | None = None, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        if type not in EVENT_TYPES:
            raise ValueError(f"unknown event type {type!r}")
        ts = time.time()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO events (run_id, ts, node, agent, type, summary, payload) VALUES (?,?,?,?,?,?,?)",
                (run_id, ts, node, agent, type, summary[:2000], _json(payload or {})),
            )
            self._conn.commit()
            event_id = cur.lastrowid
        return {"id": event_id, "run_id": run_id, "ts": ts, "node": node, "agent": agent,
                "type": type, "summary": summary, "payload": payload or {}}

    def events(self, run_id: str, after_id: int = 0, limit: int = 1000,
               types: Iterable[str] | None = None) -> list[dict[str, Any]]:
        query = "SELECT * FROM events WHERE run_id=? AND id>?"
        args: list[Any] = [run_id, after_id]
        if types:
            types = list(types)
            query += f" AND type IN ({','.join('?' * len(types))})"
            args += types
        query += " ORDER BY id LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._conn.execute(query, args).fetchall()
        return [_event_row(r) for r in rows]


def _alive(pid: int | None) -> bool:
    if not pid or pid == os.getpid():
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _run_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["options"] = json.loads(out.get("options") or "{}")
    out["summary"] = json.loads(out.get("summary") or "{}")
    return out


def _event_row(row: sqlite3.Row) -> dict[str, Any]:
    out = dict(row)
    out["payload"] = json.loads(out.get("payload") or "{}")
    return out


_default_store: EventStore | None = None
_default_lock = threading.Lock()


def default_store() -> EventStore:
    global _default_store
    with _default_lock:
        if _default_store is None or _default_store.path != db_path():
            _default_store = EventStore()
        return _default_store

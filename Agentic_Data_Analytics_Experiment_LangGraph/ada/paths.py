"""Filesystem layout.

var/                      runtime state (gitignored)
  ada.db                  runs + events (SQLite)
  checkpoints.db          LangGraph checkpointer
  mlflow.db               MLflow tracking store
  runs/<run_id>/          per-run workspace — the ONLY place agents and sandboxed code can read/write
~/.ada_vault/<run_id>/    locked holdout — mode 0700, never mounted into the sandbox
improvements.jsonl        self-improvement log (project root, committed)
"""
from __future__ import annotations

import os
from pathlib import Path

from ada.config import PROJECT_ROOT

WORKSPACE_DIR_MODE = 0o777   # the sandbox user must be able to write here
WORKSPACE_FILE_MODE = 0o666
VAULT_DIR_MODE = 0o700


def var_dir() -> Path:
    raw = os.environ.get("ADA_VAR_DIR") or str(PROJECT_ROOT / "var")
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def runs_dir() -> Path:
    path = var_dir() / "runs"
    path.mkdir(parents=True, exist_ok=True)
    # runs/ itself must be traversable by the sandbox user, but not writable
    os.chmod(path, 0o755)
    return path


def vault_root() -> Path:
    raw = os.environ.get("ADA_VAULT_DIR") or "~/.ada_vault"
    path = Path(raw).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, VAULT_DIR_MODE)
    return path


def db_path() -> Path:
    return var_dir() / "ada.db"


def checkpoint_db_path() -> Path:
    return var_dir() / "checkpoints.db"


def improvements_log() -> Path:
    return PROJECT_ROOT / "improvements.jsonl"


def prompts_dir() -> Path:
    return PROJECT_ROOT / "prompts"


def datasets_dir() -> Path:
    return PROJECT_ROOT / "datasets"

"""Test isolation: every test session uses its own var dir and vault (never the
real ones). The var dir must be traversable by the sandbox user, the vault not."""
from __future__ import annotations

import os
import shutil
import tempfile
import uuid
from pathlib import Path

import pytest

_SESSION = uuid.uuid4().hex[:8]
VAR = Path(tempfile.gettempdir()) / f"ada-test-var-{_SESSION}"
VAULT = Path(tempfile.gettempdir()) / f"ada-test-vault-{_SESSION}"
VAR.mkdir(mode=0o755)
os.chmod(VAR, 0o755)
os.environ["ADA_VAR_DIR"] = str(VAR)
os.environ["ADA_VAULT_DIR"] = str(VAULT)


@pytest.fixture(scope="session", autouse=True)
def _cleanup():
    yield
    shutil.rmtree(VAR, ignore_errors=True)
    shutil.rmtree(VAULT, ignore_errors=True)


@pytest.fixture
def cfg():
    from ada.config import Config
    return Config.load({"stub": {"delay": 0}})


@pytest.fixture
def ctx(cfg):
    """A real RunContext on a fresh workspace (no LLM)."""
    from ada.budget import BudgetTracker
    from ada.context import RunContext
    from ada.events import default_store
    from ada.store import RunStore
    from ada.vault import HoldoutVault
    run_id = f"test-{uuid.uuid4().hex[:8]}"
    events = default_store()
    events.create_run(run_id, "test objective", None, "stub", {}, "test")
    return RunContext(run_id=run_id, cfg=cfg, events=events, budget=BudgetTracker(cfg),
                      store=RunStore(run_id).init(), vault=HoldoutVault(run_id, cfg), mode="stub")

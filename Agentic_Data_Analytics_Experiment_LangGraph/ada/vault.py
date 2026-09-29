"""Locked holdout test set (protected).

After `prepare_store` first passes its gate, the orchestrator — in code, not an
agent — splits off a final holdout by a salted hash of `_row_id`, writes it to
the vault (`~/.ada_vault/<run_id>/`, mode 0700 owned by the backend user) and
strips those rows from every table in the workspace. Because the split is a
pure function of the row id, rebuilding clean data later keeps the same rows
in the holdout. The vault is never mounted into / readable by the sandbox, and
no agent tool accepts a path outside the run workspace.

The final model is scored on the holdout once, at the end (`final_evaluate`).
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import pandas as pd

from ada.config import Config
from ada.evaluation.harness import score_holdout
from ada.paths import VAULT_DIR_MODE, vault_root
from ada.sandbox.executor import Sandbox
from ada.store import RunStore

TABULAR_SUFFIXES = {".parquet", ".csv"}
STRIP_DIRS = ["clean", "data", "models", "eda", "evaluation"]


class HoldoutAlreadyUsed(RuntimeError):
    pass


def _bucket(salt: str, row_id: str, purpose: str) -> float:
    digest = hashlib.sha256(f"{salt}|{purpose}|{row_id}".encode()).hexdigest()
    return int(digest[:12], 16) / float(16 ** 12)


class HoldoutVault:
    def __init__(self, run_id: str, cfg: Config):
        self.run_id = run_id
        self.fraction = float(cfg.get("holdout.fraction", 0.2))
        self.val_fraction = float(cfg.get("holdout.val_fraction", 0.2))
        self.salt = str(cfg.get("holdout.salt", "ada"))
        self.dir = vault_root() / run_id

    # -- split rules -----------------------------------------------------
    def is_holdout(self, row_id: Any) -> bool:
        return _bucket(self.salt, str(row_id), "holdout") < self.fraction

    def is_val(self, row_id: Any) -> bool:
        return _bucket(self.salt, str(row_id), "val") < self.val_fraction

    @property
    def locked(self) -> bool:
        return (self.dir / "lock.json").exists()

    def _ensure_dir(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        os.chmod(self.dir, VAULT_DIR_MODE)

    # -- lock + strip ------------------------------------------------------
    def lock(self, store: RunStore, *, target: str) -> dict[str, Any]:
        self._ensure_dir()
        info = {"run_id": self.run_id, "target": target, "fraction": self.fraction,
                "locked_at": time.time()}
        (self.dir / "lock.json").write_text(json.dumps(info))
        stats = self.strip_workspace(store)
        info.update(stats)
        (self.dir / "lock.json").write_text(json.dumps(info))
        return info

    def strip_workspace(self, store: RunStore) -> dict[str, Any]:
        """Remove holdout rows from every table under the stripped dirs. If clean.parquet
        still contains holdout rows (fresh rebuild), those rows become the new holdout
        version. Then rewrite data/splits.json. No-op before the lock."""
        if not self.locked:
            return {}
        target = json.loads((self.dir / "lock.json").read_text())["target"]
        stats: dict[str, Any] = {"stripped_files": []}
        for sub in STRIP_DIRS:
            base = store.resolve(sub)
            if not base.exists():
                continue
            for path in base.rglob("*"):
                if path.suffix.lower() not in TABULAR_SUFFIXES or not path.is_file():
                    continue
                try:
                    df = pd.read_parquet(path) if path.suffix == ".parquet" else pd.read_csv(path, low_memory=False)
                except Exception:
                    continue
                if "_row_id" not in df.columns:
                    continue
                mask = df["_row_id"].astype(str).map(self.is_holdout)
                if not mask.any():
                    continue
                if store.rel(path) == "clean/clean.parquet":
                    hold = df[mask].copy()
                    hold_path = self.dir / "holdout.parquet"
                    hold.to_parquet(hold_path, index=False)
                    os.chmod(hold_path, 0o600)
                    stats["n_holdout"] = int(len(hold))
                    stats["target_in_holdout"] = target in hold.columns
                kept = df[~mask]
                tmp = path.with_name(f".{path.name}.strip")
                if path.suffix == ".parquet":
                    kept.to_parquet(tmp, index=False)
                else:
                    kept.to_csv(tmp, index=False)
                os.chmod(tmp, 0o666)
                os.replace(tmp, path)
                stats["stripped_files"].append({"path": store.rel(path), "removed": int(mask.sum())})
        self.write_splits(store)
        return stats

    def write_splits(self, store: RunStore) -> dict[str, Any] | None:
        if not store.exists("clean/clean.parquet"):
            return None
        ids = pd.read_parquet(store.resolve("clean/clean.parquet"), columns=["_row_id"])["_row_id"].astype(str)
        ids = [i for i in ids if not self.is_holdout(i)]
        val = [i for i in ids if self.is_val(i)]
        train = [i for i in ids if not self.is_val(i)]
        splits = {"train_ids": train, "val_ids": val, "rule": "salted sha256 of _row_id (fixed by orchestrator)",
                  "n_train": len(train), "n_val": len(val)}
        store.write_json("data/splits.json", splits)
        return splits

    def holdout_ids(self) -> set[str]:
        path = self.dir / "holdout.parquet"
        if not path.exists():
            return set()
        return set(pd.read_parquet(path, columns=["_row_id"])["_row_id"].astype(str))

    # -- the one final evaluation -----------------------------------------
    @property
    def evaluated(self) -> bool:
        return (self.dir / "evaluated.json").exists()

    def final_evaluate(self, store: RunStore, sandbox: Sandbox, *, task_type: str, target: str) -> dict[str, Any]:
        if not self.locked:
            raise HoldoutAlreadyUsed("holdout was never locked")
        marker = self.dir / "evaluated.json"
        if marker.exists():
            raise HoldoutAlreadyUsed("the locked holdout has already been used for the final evaluation")
        marker.write_text(json.dumps({"started_at": time.time()}))
        holdout = pd.read_parquet(self.dir / "holdout.parquet")
        result = score_holdout(store, sandbox, holdout, task_type=task_type, target=target)
        result["evaluated_at"] = time.time()
        marker.write_text(json.dumps(result, default=float))
        return result

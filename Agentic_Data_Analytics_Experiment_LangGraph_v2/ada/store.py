"""Shared artifact store: one workspace directory per run.

All agent file access goes through `RunStore.resolve()`, which rejects absolute
paths, `..` traversal and symlinks leaving the workspace. The holdout vault is
outside the workspace, so no agent tool can name it.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

from ada.paths import WORKSPACE_DIR_MODE, WORKSPACE_FILE_MODE, runs_dir

STANDARD_DIRS = ["raw", "clean", "data", "eda/charts", "models", "evaluation", "report", "code", ".cache"]


class PathDenied(PermissionError):
    pass


class RunStore:
    def __init__(self, run_id: str, root: Path | None = None):
        self.run_id = run_id
        self.root = (root or runs_dir() / run_id).resolve()

    def init(self) -> "RunStore":
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, WORKSPACE_DIR_MODE)
        for sub in STANDARD_DIRS:
            self.ensure_dir(sub)
        return self

    # -- path guard ------------------------------------------------------
    def resolve(self, rel: str | os.PathLike[str], *, must_exist: bool = False) -> Path:
        rel_str = str(rel).strip()
        if not rel_str:
            raise PathDenied("empty path")
        candidate = Path(rel_str)
        if candidate.is_absolute():
            # allow absolute paths that already point inside the workspace
            target = candidate.resolve()
        else:
            target = (self.root / candidate).resolve()
        if target != self.root and self.root not in target.parents:
            raise PathDenied(f"path {rel_str!r} is outside the run workspace")
        if must_exist and not target.exists():
            raise FileNotFoundError(rel_str)
        return target

    def rel(self, path: Path) -> str:
        return str(Path(path).resolve().relative_to(self.root))

    def ensure_dir(self, rel: str) -> Path:
        path = self.resolve(rel)
        path.mkdir(parents=True, exist_ok=True)
        # make every level (up to the root) writable for the sandbox user
        node = path
        while node != self.root and self.root in node.parents:
            try:
                os.chmod(node, WORKSPACE_DIR_MODE)
            except PermissionError:
                pass
            node = node.parent
        return path

    # -- io ----------------------------------------------------------------
    def write_text(self, rel: str, text: str) -> Path:
        return self.write_bytes(rel, text.encode("utf-8"))

    def write_bytes(self, rel: str, data: bytes) -> Path:
        path = self.resolve(rel)
        self.ensure_dir(str(path.parent.relative_to(self.root)) if path.parent != self.root else ".")
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_bytes(data)
        os.chmod(tmp, WORKSPACE_FILE_MODE)
        os.replace(tmp, path)
        return path

    def write_json(self, rel: str, obj: Any) -> Path:
        return self.write_text(rel, json.dumps(obj, indent=2, default=str, ensure_ascii=False))

    def read_json(self, rel: str, default: Any = None) -> Any:
        try:
            return json.loads(self.resolve(rel, must_exist=True).read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def read_text(self, rel: str, max_chars: int | None = None) -> str:
        text = self.resolve(rel, must_exist=True).read_text(encoding="utf-8", errors="replace")
        return text if max_chars is None else text[:max_chars]

    def exists(self, rel: str) -> bool:
        try:
            return self.resolve(rel).exists()
        except PathDenied:
            return False

    def copy_in(self, src: Path, rel: str) -> Path:
        dst = self.resolve(rel)
        self.ensure_dir(str(dst.parent.relative_to(self.root)))
        shutil.copyfile(src, dst)
        os.chmod(dst, WORKSPACE_FILE_MODE)
        return dst

    def list_files(self, rel: str = ".", *, include_hidden: bool = False, limit: int = 500) -> list[dict[str, Any]]:
        base = self.resolve(rel)
        if not base.exists():
            return []
        out = []
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relp = self.rel(path)
            if not include_hidden and any(part.startswith(".") for part in Path(relp).parts):
                continue
            stat = path.stat()
            out.append({"path": relp, "bytes": stat.st_size, "mtime": stat.st_mtime})
            if len(out) >= limit:
                break
        return out

    def snapshot_mtimes(self) -> dict[str, float]:
        return {f["path"]: f["mtime"] for f in self.list_files(include_hidden=False, limit=100_000)}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

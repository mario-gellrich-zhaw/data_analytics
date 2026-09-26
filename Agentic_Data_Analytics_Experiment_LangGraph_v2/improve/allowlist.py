"""The improver's editable surface, enforced in code (protected: the improver
cannot edit anything under improve/).

Allowed:  prompts/*.md, agents/tools/*.py, config.yaml (only the keys below).
Everything else — gates, evaluation code, the holdout vault, budgets, benchmark
definitions, scoring and these acceptance rules — is off-limits.
"""
from __future__ import annotations

import ast
import fnmatch
from pathlib import PurePosixPath
from typing import Any

import yaml

EDITABLE_GLOBS = ["prompts/*.md", "agents/tools/*.py"]
EDITABLE_CONFIG_KEYS = {"models", "roles", "agents"}
EDITABLE_CONFIG_SUBKEYS = {"web": {"request_delay_seconds"}}
# tokens that tool code must not introduce: reaching the vault/holdout, gate or evaluation
# internals, monkeypatching, spawning processes or reading secrets
FORBIDDEN_TOOL_TOKENS = [
    "vault", "holdout", "ADA_VAULT", "ada.gates", "ada.evaluation", "ada.vault", "ada.budget", "from improve", "import improve",
    "benchmarks", "splits.json", "evaluated.json", "os.environ", "getenv", "subprocess", "ctypes", "importlib",
    "__import__", "sys.modules", "setattr(", "globals(", "exec(", "eval(", "OPENAI_API_KEY", ".env",
]


class ChangeRejected(ValueError):
    pass


def normalise(path: str) -> str:
    p = PurePosixPath(path.replace("\\", "/"))
    if p.is_absolute() or ".." in p.parts:
        raise ChangeRejected(f"{path}: absolute paths and '..' are not allowed")
    return str(p)


def is_editable(path: str) -> bool:
    p = normalise(path)
    return p == "config.yaml" or any(fnmatch.fnmatchcase(p, g) for g in EDITABLE_GLOBS)


def check_config_change(old_text: str, new_text: str) -> None:
    old = yaml.safe_load(old_text) or {}
    new = yaml.safe_load(new_text)
    if not isinstance(new, dict):
        raise ChangeRejected("config.yaml must stay a mapping")
    for key in set(old) | set(new):
        if old.get(key) == new.get(key):
            continue
        if key in EDITABLE_CONFIG_KEYS:
            continue
        if key in EDITABLE_CONFIG_SUBKEYS and isinstance(old.get(key), dict) and isinstance(new.get(key), dict):
            allowed = EDITABLE_CONFIG_SUBKEYS[key]
            for sub in set(old[key]) | set(new[key]):
                if old[key].get(sub) != new[key].get(sub) and sub not in allowed:
                    raise ChangeRejected(f"config.yaml: {key}.{sub} is protected")
            continue
        raise ChangeRejected(f"config.yaml: top-level key '{key}' is protected (budgets, gates, holdout, sandbox, "
                             "pricing, improve and paths cannot be changed by the improver)")


def check_tool_change(path: str, old_text: str, new_text: str) -> None:
    try:
        ast.parse(new_text)
    except SyntaxError as exc:
        raise ChangeRejected(f"{path}: syntax error: {exc}") from exc
    old_lines = set(old_text.splitlines())
    added = "\n".join(line for line in new_text.splitlines() if line not in old_lines)
    lowered = added.lower()
    for token in FORBIDDEN_TOOL_TOKENS:
        if token.lower() in lowered:
            raise ChangeRejected(f"{path}: new code references forbidden token {token!r}")


def check_change(path: str, old_text: str | None, new_text: str) -> str:
    """Raise ChangeRejected unless this file change is inside the editable surface."""
    p = normalise(path)
    if not is_editable(p):
        raise ChangeRejected(f"{p} is outside the editable surface ({', '.join(EDITABLE_GLOBS)}, config.yaml)")
    if p == "config.yaml":
        check_config_change(old_text or "", new_text)
    elif p.endswith(".py"):
        check_tool_change(p, old_text or "", new_text)
    elif p.startswith("prompts/") and not new_text.strip():
        raise ChangeRejected(f"{p}: empty prompt")
    return p


def apply_change(change: dict[str, Any], read: Any) -> tuple[str, str | None, str]:
    """Turn a FileChange (replace_file | search_replace) into (path, old, new) after validation."""
    path = normalise(change["path"])
    old = read(path)
    if change["kind"] == "replace_file":
        new = change.get("content") or ""
    else:
        if old is None:
            raise ChangeRejected(f"{path}: search_replace on a missing file")
        search, replace = change.get("search") or "", change.get("replace") or ""
        if not search or old.count(search) != 1:
            raise ChangeRejected(f"{path}: search text must occur exactly once (found {old.count(search) if search else 0})")
        new = old.replace(search, replace)
    check_change(path, old, new)
    return path, old, new

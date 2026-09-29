"""Local dataset catalog (datasets/catalog.yaml) — used for offline runs and
benchmarks, so benchmarking is cheap and reproducible without web access."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import yaml
from pydantic import BaseModel, Field

from agents.tools.registry import ToolContext, tool
from agents.tools.web import _record_source
from ada.paths import datasets_dir


def _catalog(tc: ToolContext) -> list[dict[str, Any]]:
    path = datasets_dir() / "catalog.yaml"
    if not path.exists():
        return []
    entries = (yaml.safe_load(path.read_text()) or {}).get("datasets", [])
    # benchmark-only datasets are hidden from normal online runs so they don't replace real sourcing
    return [e for e in entries if tc.ctx.offline or not e.get("benchmark_only")]


class NoArgs(BaseModel):
    pass


@tool("list_local_datasets", "List datasets available locally (name, description, license, columns).", NoArgs)
def list_local_datasets(tc: ToolContext, a: NoArgs) -> Any:
    entries = _catalog(tc)
    return [{k: e.get(k) for k in ("name", "description", "license", "rows", "columns")} for e in entries] \
        or "no local datasets available"


class ImportArgs(BaseModel):
    name: str = Field(description="dataset name from list_local_datasets")


@tool("import_local_dataset", "Copy a local dataset into raw/ with provenance.", ImportArgs)
def import_local_dataset(tc: ToolContext, a: ImportArgs) -> Any:
    entry = next((e for e in _catalog(tc) if e["name"] == a.name), None)
    if entry is None:
        return f"unknown dataset {a.name!r}"
    src = datasets_dir() / entry["file"]
    rel = f"raw/{entry['file']}"
    tc.ctx.store.copy_in(src, rel)
    _record_source(tc, rel, {
        "url": entry.get("source_url", f"local://datasets/{entry['file']}"), "source_name": entry["name"],
        "license": entry.get("license", "unknown"), "license_url": entry.get("license_url", ""),
        "description": entry.get("description", ""), "bytes": src.stat().st_size,
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    tc.ctx.emit("artifact", f"imported local dataset {a.name} -> {rel}", node=tc.node, agent=tc.agent,
                payload={"path": rel, "license": entry.get("license")})
    return {"saved": rel, "columns": entry.get("columns"), "notes": entry.get("notes", "")}

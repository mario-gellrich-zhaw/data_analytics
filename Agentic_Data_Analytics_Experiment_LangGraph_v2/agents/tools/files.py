"""Workspace file tools. All paths are resolved by RunStore.resolve(), which
rejects anything outside the run workspace (the holdout vault is outside)."""
from __future__ import annotations

from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from agents.tools.registry import ToolContext, tool

PROTECTED_WRITES = ("data/splits.json", "raw/sources.json", "experiments.jsonl", "models/", "evaluation/val_")


class ListFilesArgs(BaseModel):
    subdir: str = Field(".", description="directory relative to the workspace")


@tool("list_files", "List files in the run workspace (relative paths, sizes).", ListFilesArgs)
def list_files(tc: ToolContext, a: ListFilesArgs) -> Any:
    files = tc.ctx.store.list_files(a.subdir, limit=300)
    return [{"path": f["path"], "kb": round(f["bytes"] / 1024, 1)} for f in files] or "no files"


class ReadFileArgs(BaseModel):
    path: str
    offset: int = Field(0, ge=0, description="character offset")
    max_chars: int = Field(4000, ge=100, le=20000)


@tool("read_file", "Read a text file (markdown, json, csv head, python) from the workspace.", ReadFileArgs)
def read_file(tc: ToolContext, a: ReadFileArgs) -> Any:
    path = tc.ctx.store.resolve(a.path, must_exist=True)
    if path.suffix in (".parquet", ".pkl", ".png", ".zip", ".xlsx", ".gz"):
        return f"{a.path} is binary — use preview_table or run_python"
    text = path.read_text(encoding="utf-8", errors="replace")
    chunk = text[a.offset:a.offset + a.max_chars]
    more = len(text) - (a.offset + len(chunk))
    return chunk + (f"\n...[{more} more chars; use offset={a.offset + len(chunk)}]" if more > 0 else "")


class WriteFileArgs(BaseModel):
    path: str = Field(description="e.g. data_card.md, eda/eda_report.md, report/final_report.md")
    content: str


@tool("write_file", "Write a text/markdown file into the workspace (reports, data card, notes).", WriteFileArgs)
def write_file(tc: ToolContext, a: WriteFileArgs) -> Any:
    rel = tc.ctx.store.rel(tc.ctx.store.resolve(a.path))
    if any(rel == p or rel.startswith(p) for p in PROTECTED_WRITES):
        return f"refused: {rel} is managed by the orchestrator"
    if rel.endswith((".py", ".pkl", ".parquet")):
        return "refused: use run_python for code and data files"
    tc.ctx.store.write_text(rel, a.content)
    tc.ctx.emit("artifact", f"wrote {rel}", node=tc.node, agent=tc.agent, payload={"path": rel})
    return f"wrote {rel} ({len(a.content)} chars)"


class PreviewArgs(BaseModel):
    path: str
    rows: int = Field(8, ge=1, le=50)


def _load_table(path, nrows: int | None = None) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix in (".xlsx", ".xls"):
        return pd.read_excel(path, nrows=nrows)
    if suffix in (".json", ".geojson"):
        try:
            return pd.read_json(path)
        except ValueError:
            import json
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            for key in ("results", "data", "features", "items", "records"):
                if isinstance(data, dict) and isinstance(data.get(key), list):
                    return pd.json_normalize(data[key])
            return pd.json_normalize(data)
    sep = "\t" if suffix == ".tsv" else None
    return pd.read_csv(path, sep=sep, engine="python", nrows=nrows, on_bad_lines="skip")


@tool("preview_table", "Shape, dtypes, missing rates and first rows of a tabular file (csv/tsv/parquet/json/xlsx).",
      PreviewArgs)
def preview_table(tc: ToolContext, a: PreviewArgs) -> Any:
    path = tc.ctx.store.resolve(a.path, must_exist=True)
    df = _load_table(path, nrows=20000)
    info = {
        "shape": list(df.shape) if path.suffix == ".parquet" else f"{df.shape} (first 20000 rows read)",
        "dtypes": {c: str(t) for c, t in list(df.dtypes.items())[:60]},
        "missing_rate": {c: round(float(v), 3) for c, v in df.isna().mean().items() if v > 0},
        "head": df.head(a.rows).to_dict(orient="records"),
    }
    return info

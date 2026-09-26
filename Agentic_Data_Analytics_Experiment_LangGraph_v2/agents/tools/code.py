"""run_python: agents write Python that executes in the sandbox (no network, no
secrets, cwd = run workspace, `import ada_kit` available)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agents.tools.registry import ToolContext, tool

ARTIFACT_PREFIXES = ("clean/", "eda/", "models/", "evaluation/", "report/", "data_card.md", "experiments.jsonl")


class RunPythonArgs(BaseModel):
    purpose: str = Field(description="one line: what this script does")
    code: str = Field(description="complete Python script; read/write files relative to the workspace")
    timeout_seconds: int = Field(300, ge=10, le=1800)


def _tail(text: str, n: int) -> str:
    return text if len(text) <= n else "...[earlier output cut]\n" + text[-n:]


@tool("run_python",
      "Execute a Python script in the sandbox. cwd is the run workspace. pandas, numpy, scikit-learn, "
      "lightgbm, shap, matplotlib, duckdb, pyarrow and `ada_kit` are available. No network access. "
      "Print what you need to see (keep output short).",
      RunPythonArgs)
def run_python(tc: ToolContext, a: RunPythonArgs) -> Any:
    ctx = tc.ctx
    counter = len(ctx.store.list_files("code")) + 1
    rel = f"code/{counter:03d}_{tc.node}.py"
    header = f"# {tc.agent} — {a.purpose}\n"
    ctx.store.write_text(rel, header + a.code)
    res = ctx.sandbox.run_script(ctx.store.root, ctx.store.resolve(rel), timeout=a.timeout_seconds)
    # the orchestrator immediately removes holdout rows from anything the script wrote
    stripped = ctx.vault.strip_workspace(ctx.store) if ctx.vault.locked else {}
    changed = [p for p in res.changed_files if not p.startswith(("code/",))]
    for path in changed:
        if path.startswith(ARTIFACT_PREFIXES):
            ctx.emit("artifact", f"{tc.agent} wrote {path}", node=tc.node, agent=tc.agent, payload={"path": path})
    out = {
        "script": rel,
        "ok": res.ok,
        "returncode": res.returncode,
        "timed_out": res.timed_out,
        "seconds": round(res.duration, 1),
        "stdout": _tail(res.stdout, 3500),
        "stderr": _tail(res.stderr, 2500) if res.stderr.strip() else "",
        "files_written": changed[:40],
    }
    if stripped.get("stripped_files"):
        out["note"] = "holdout rows were removed from: " + ", ".join(f["path"] for f in stripped["stripped_files"])
    return out

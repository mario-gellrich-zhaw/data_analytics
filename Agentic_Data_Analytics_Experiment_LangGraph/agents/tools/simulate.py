"""simulate_dataset: generate a synthetic raw table with a seeded script when the
objective explicitly asks for simulated data (ObjectiveSpec.data_origin == "simulated").

The script runs in the sandbox (no network) twice; both runs must produce the same
file, so the data is reproducible from the recorded script + seed. Provenance goes
to raw/sources.json like any download (url = simulation://<script>).
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from agents.tools.files import _load_table
from agents.tools.registry import ToolContext, tool
from agents.tools.web import _record_source
from ada.store import sha256_file

SIM_LICENSE = "synthetic — generated in this run, no third-party rights"


class SimulateArgs(BaseModel):
    filename: str = Field(description="output file name in raw/, .csv or .parquet (e.g. simulated_car_listings.csv)")
    purpose: str = Field(description="one line: what is simulated and how (groups, distributions, effect sizes)")
    seed: int = Field(description="random seed; the script must take all randomness from SEED")
    code: str = Field(description="complete Python script. OUTPUT_PATH and SEED are predefined; build a DataFrame "
                                  "(e.g. rng = numpy.random.default_rng(SEED)) and write it to OUTPUT_PATH. "
                                  "Print a short summary (rows, group means).")
    design: dict[str, Any] = Field(default_factory=dict,
                                   description="simulation parameters recorded with the provenance "
                                               "(e.g. groups, n per group, means, SDs, distributions)")


def _objective_origin(tc: ToolContext) -> str:
    spec = tc.ctx.store.read_json("objective_spec.json", default={}) or {}
    return spec.get("data_origin", "collected")


def _run(tc: ToolContext, script_rel: str, timeout: int):
    ctx = tc.ctx
    return ctx.sandbox.run_script(ctx.store.root, ctx.store.resolve(script_rel), timeout=timeout)


@tool("simulate_dataset",
      "Generate a synthetic dataset into raw/ with a seeded Python script (sandbox, no network) and record its "
      "provenance. Only allowed when the objective's data_origin is 'simulated'. The script is run twice and must "
      "produce identical output.",
      SimulateArgs)
def simulate_dataset(tc: ToolContext, a: SimulateArgs) -> Any:
    store = tc.ctx.store
    if _objective_origin(tc) != "simulated":
        return "refused: the objective does not ask for simulated data (data_origin != 'simulated') — collect real data"
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", a.filename).strip("._")
    if not name.endswith((".csv", ".parquet")):
        return "refused: filename must end in .csv or .parquet"
    rel = f"raw/{name}"
    store.ensure_dir("raw")
    # the script must not touch the provenance file; restore it whatever the script does
    sources_before = store.read_json("raw/sources.json", default=None)

    counter = len(store.list_files("code")) + 1
    script_rel = f"code/{counter:03d}_{tc.node}_simulate.py"
    header = (f"# {tc.agent} — simulate_dataset: {a.purpose}\n"
              f"OUTPUT_PATH = {rel!r}\nSEED = {a.seed}\n\n")
    store.write_text(script_rel, header + a.code)

    hashes, res = [], None
    for _ in range(2):
        if store.exists(rel):
            store.resolve(rel).unlink()
        res = _run(tc, script_rel, int(tc.ctx.cfg.get("sandbox.timeout_seconds", 300)))
        if sources_before is not None:
            store.write_json("raw/sources.json", sources_before)
        if not res.ok or not store.exists(rel):
            error = "script failed" if not res.ok else f"script did not write {rel}"
            if store.exists(rel):
                store.resolve(rel).unlink()
            return {"ok": False, "script": script_rel, "error": error, "returncode": res.returncode,
                    "timed_out": res.timed_out, "stdout": res.stdout[-2000:], "stderr": res.stderr[-2500:]}
        hashes.append(sha256_file(store.resolve(rel)))
    if hashes[0] != hashes[1]:
        store.resolve(rel).unlink()
        return {"ok": False, "script": script_rel,
                "error": "output differs between two runs — take all randomness from SEED "
                         "(numpy.random.default_rng(SEED)), no wall-clock times or unseeded ids"}

    df = _load_table(store.resolve(rel))
    stray = [p for p in res.changed_files if p.startswith("raw/") and p not in (rel, "raw/sources.json")]
    _record_source(tc, rel, {
        "url": f"simulation://{script_rel}", "source_name": "simulation", "license": SIM_LICENSE,
        "license_url": "", "description": a.purpose, "bytes": store.resolve(rel).stat().st_size,
        "generator": "simulate_dataset", "script": script_rel, "script_sha256": sha256_file(store.resolve(script_rel)),
        "seed": a.seed, "design": a.design, "rows": int(len(df)), "columns": [str(c) for c in df.columns],
        "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    tc.ctx.emit("artifact", f"simulated {rel} ({len(df)} rows, seed {a.seed})", node=tc.node, agent=tc.agent,
                payload={"path": rel, "script": script_rel, "license": SIM_LICENSE})
    out = {"ok": True, "saved": rel, "script": script_rel, "rows": int(len(df)),
           "columns": [str(c) for c in df.columns][:60], "reproducible": True, "stdout": res.stdout[-2000:]}
    if stray:
        out["note"] = f"files in raw/ without provenance (remove them or they fail the gate): {stray[:10]}"
    return out

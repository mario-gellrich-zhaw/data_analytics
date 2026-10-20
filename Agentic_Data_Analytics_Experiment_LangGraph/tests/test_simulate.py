"""simulate_dataset: only for simulated objectives, reproducible, provenance passes the collect gate."""
from __future__ import annotations

import pytest

from ada.sandbox.executor import Sandbox, SandboxUnavailable

# stdlib only, so the tests don't depend on which packages the sandbox user can import
GOOD = """
import csv, random
rng = random.Random(SEED)
cities = {"Zurich": 18000, "Geneva": 17000, "Basel": 16500, "Bern": 16000, "Lausanne": 17500}
with open(OUTPUT_PATH, "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["listing_id", "city", "car_price_chf"])
    for c, m in cities.items():
        for i in range(60):
            w.writerow([f"{c[:3]}-{i}", c, round(rng.gauss(m, 3500), 2)])
"""

UNSEEDED = """
import random
with open(OUTPUT_PATH, "w") as f:
    f.write("x\\n" + "\\n".join(str(random.random()) for _ in range(100)))
"""


def _call(ctx, code, origin="simulated", filename="sim.csv"):
    from agents.tools.registry import REGISTRY, ToolContext
    ctx.store.write_json("objective_spec.json", {"data_origin": origin})
    t = REGISTRY["simulate_dataset"]
    return t.fn(ToolContext(ctx, "collect_data", "DataCollectorAgent"),
                t.params(filename=filename, purpose="5 cities x 60", seed=7, code=code, design={"n_per_city": 60}))


@pytest.fixture
def sim_ctx(ctx, cfg):
    try:
        sb = Sandbox(cfg)
    except SandboxUnavailable as exc:
        pytest.skip(str(exc))
    ctx._sandbox = sb
    return ctx


def test_refused_unless_objective_is_simulated(ctx):
    out = _call(ctx, GOOD, origin="collected")
    assert isinstance(out, str) and out.startswith("refused")
    assert not ctx.store.exists("raw/sim.csv")


def test_simulated_file_has_provenance_and_passes_gate(sim_ctx):
    from ada.gates.checks import check_collect_data
    out = _call(sim_ctx, GOOD)
    assert out["ok"], out
    assert out["rows"] == 300
    src = sim_ctx.store.read_json("raw/sources.json")[0]
    assert src["url"].startswith("simulation://code/") and src["seed"] == 7 and src["design"] == {"n_per_city": 60}
    report = {"files": [{"path": "raw/sim.csv"}], "primary_file": "raw/sim.csv",
              "license_assessment": "synthetic (own work)", "obtainable": True}
    failed = [c for c in check_collect_data(report, store=sim_ctx.store, attempts=1) if not c.passed]
    assert not failed, failed


def test_unseeded_output_is_rejected(sim_ctx):
    out = _call(sim_ctx, UNSEEDED)
    assert not out["ok"] and "SEED" in out["error"]
    assert not sim_ctx.store.exists("raw/sim.csv")
    assert not sim_ctx.store.read_json("raw/sources.json", default=[])

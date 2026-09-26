"""The locked holdout cannot be read by agents: not through tools (path guard),
not from sandboxed code (OS permissions + audit hook), and it is used once."""
from __future__ import annotations

import os
import textwrap

import numpy as np
import pandas as pd
import pytest

from ada.sandbox.executor import Sandbox, SandboxUnavailable
from ada.store import PathDenied
from ada.vault import HoldoutAlreadyUsed
from agents.tools import REGISTRY, ToolContext


def _clean(ctx, n=400):
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"_row_id": [f"r{i}" for i in range(n)], "x": rng.normal(size=n), "cat": rng.choice(list("abc"), n)})
    df["y"] = 3 * df["x"] + rng.normal(size=n)
    path = ctx.store.resolve("clean/clean.parquet")
    df.to_parquet(path, index=False)
    path.chmod(0o666)
    return df


@pytest.fixture
def locked(ctx):
    df = _clean(ctx)
    info = ctx.vault.lock(ctx.store, target="y")
    return ctx, df, info


def _sandbox_or_skip(cfg):
    try:
        sb = Sandbox(cfg)
    except SandboxUnavailable as exc:
        pytest.skip(str(exc))
    if not sb.isolated:
        pytest.skip("no isolated sandbox available")
    return sb


def test_lock_moves_rows_to_vault_and_strips_workspace(locked):
    ctx, df, info = locked
    hold_ids = ctx.vault.holdout_ids()
    assert 40 < len(hold_ids) < 130                     # ~20 % of 400
    remaining = pd.read_parquet(ctx.store.resolve("clean/clean.parquet"))
    assert not set(remaining["_row_id"]) & hold_ids
    assert len(remaining) + len(hold_ids) == len(df)
    splits = ctx.store.read_json("data/splits.json")
    assert not set(splits["train_ids"]) & set(splits["val_ids"])
    assert not (set(splits["train_ids"]) | set(splits["val_ids"])) & hold_ids
    assert oct(ctx.vault.dir.stat().st_mode & 0o777) == "0o700"


def test_split_is_stable_across_rebuilds(locked):
    ctx, df, _ = locked
    before = ctx.vault.holdout_ids()
    _clean(ctx)                                      # agent rebuilds clean data from raw
    ctx.vault.strip_workspace(ctx.store)
    assert ctx.vault.holdout_ids() == before


def test_store_rejects_paths_outside_workspace(locked):
    ctx, _, _ = locked
    for bad in [str(ctx.vault.dir / "holdout.parquet"), "../../x", "/etc/passwd", "raw/../../.."]:
        with pytest.raises(PathDenied):
            ctx.store.resolve(bad)


def test_agent_tools_cannot_read_holdout(locked):
    ctx, _, _ = locked
    tc = ToolContext(ctx, "modeling", "ModelingAgent")
    target = str(ctx.vault.dir / "holdout.parquet")
    for name, args in [("read_file", {"path": target}), ("preview_table", {"path": target}),
                       ("list_files", {"subdir": str(ctx.vault.dir)})]:
        tool = REGISTRY[name]
        with pytest.raises(PathDenied):
            tool.fn(tc, tool.params(**args))


def test_sandbox_code_cannot_read_holdout(locked, cfg):
    ctx, _, _ = locked
    sb = _sandbox_or_skip(cfg)
    hold = ctx.vault.dir / "holdout.parquet"
    script = ctx.store.write_text("code/attack.py", textwrap.dedent(f"""
        import os
        results = []
        for attempt in ("open", "pandas", "listdir", "pyarrow"):
            try:
                if attempt == "open":
                    open({str(hold)!r}, "rb").read()
                elif attempt == "pandas":
                    import pandas as pd; pd.read_parquet({str(hold)!r})
                elif attempt == "listdir":
                    os.listdir({str(ctx.vault.dir)!r})
                else:
                    import pyarrow.parquet as pq; pq.read_table({str(hold)!r})
                results.append(attempt + ":LEAK")
            except Exception as exc:
                results.append(attempt + ":blocked:" + type(exc).__name__)
        print("|".join(results))
    """))
    res = sb.run_script(ctx.store.root, script)
    assert res.ok, res.stderr
    assert "LEAK" not in res.stdout, res.stdout
    assert res.stdout.count("blocked") == 4


def test_sandbox_has_no_secrets_and_no_network(ctx, cfg):
    sb = _sandbox_or_skip(cfg)
    os.environ.setdefault("OPENAI_API_KEY", "sk-test-not-real")
    script = ctx.store.write_text("code/probe.py", textwrap.dedent("""
        import os, socket
        print("KEY" if any("OPENAI" in k or "TAVILY" in k for k in os.environ) else "nokey")
        try:
            socket.create_connection(("example.org", 80), timeout=5); print("NET")
        except Exception as exc:
            print("nonet", type(exc).__name__)
        try:
            open("/workspaces/owned.txt", "w").write("x"); print("WROTE_OUTSIDE")
        except Exception:
            print("nowrite")
    """))
    res = sb.run_script(ctx.store.root, script)
    assert "nokey" in res.stdout and "nonet" in res.stdout and "nowrite" in res.stdout, res.stdout + res.stderr


def test_sandbox_timeout(ctx, cfg):
    sb = _sandbox_or_skip(cfg)
    script = ctx.store.write_text("code/spin.py", "import time\nwhile True: time.sleep(0.1)\n")
    res = sb.run_script(ctx.store.root, script, timeout=3)
    assert res.timed_out and not res.ok


def test_final_evaluation_only_once(locked, cfg):
    ctx, df, _ = locked
    sb = _sandbox_or_skip(cfg)
    ctx._sandbox = sb
    model_code = textwrap.dedent("""
        import ada_kit
        from sklearn.linear_model import LinearRegression
        train, val = ada_kit.train_val()
        m = LinearRegression().fit(train[["x"]], train["y"])
        ada_kit.save_model(m, ["x"], "y", train["_row_id"], name="lr")
    """)
    res = sb.run_script(ctx.store.root, ctx.store.write_text("code/fit.py", model_code))
    assert res.ok, res.stderr
    out = ctx.vault.final_evaluate(ctx.store, sb, task_type="regression", target="y")
    assert out["n_holdout"] == len(ctx.vault.holdout_ids())
    assert out["metrics"]["mae"] < out["baseline"]["mae"]
    with pytest.raises(HoldoutAlreadyUsed):
        ctx.vault.final_evaluate(ctx.store, sb, task_type="regression", target="y")
    assert not list(ctx.store.resolve(".final_eval").glob("*"))   # holdout features cleaned up

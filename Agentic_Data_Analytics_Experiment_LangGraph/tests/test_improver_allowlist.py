"""The improver can only touch prompts/, agents/tools/ and non-budget config keys."""
from __future__ import annotations

import pytest
import yaml

from ada.config import CONFIG_PATH
from improve.allowlist import ChangeRejected, apply_change, check_change, is_editable

CONFIG = CONFIG_PATH.read_text()


@pytest.mark.parametrize("path", ["prompts/modeling.md", "agents/tools/web.py", "config.yaml"])
def test_editable_surface(path):
    assert is_editable(path)


@pytest.mark.parametrize("path", [
    "ada/gates/gate.py", "ada/gates/checks.py", "ada/evaluation/metrics.py", "ada/vault.py", "ada/budget.py",
    "improve/allowlist.py", "improve/scoring.py", "benchmarks/tasks.yaml", "datasets/diabetes.csv",
    "tests/test_budget.py", "agents/base.py", "agents/tools/../../ada/gates/gate.py", "/etc/passwd",
    "prompts/../ada/vault.py",
])
def test_protected_paths_rejected(path):
    with pytest.raises(ChangeRejected):
        check_change(path, "", "x = 1\n")


def _config_with(mutate):
    data = yaml.safe_load(CONFIG)
    mutate(data)
    return yaml.safe_dump(data)


@pytest.mark.parametrize("mutate", [
    lambda d: d["budgets"].update(max_usd=100),
    lambda d: d["gates"].update(critic_pass_score=0),
    lambda d: d["holdout"].update(fraction=0.01),
    lambda d: d["improve"].update(min_improvement=-1),
    lambda d: d["sandbox"].update(require_isolation=False),
    lambda d: d["web"].update(max_download_mb=10_000),
    lambda d: d.pop("pricing_usd_per_1m"),
])
def test_protected_config_keys_rejected(mutate):
    with pytest.raises(ChangeRejected):
        check_change("config.yaml", CONFIG, _config_with(mutate))


def test_allowed_config_keys_accepted():
    new = _config_with(lambda d: (d["models"].update(worker="gpt-5-nano"), d["agents"]["max_tool_steps"].update(eda=12),
                                  d["web"].update(request_delay_seconds=2.0)))
    assert check_change("config.yaml", CONFIG, new) == "config.yaml"


@pytest.mark.parametrize("snippet", [
    "open(os.path.expanduser('~/.ada_vault/x/holdout.parquet'))",
    "import subprocess",
    "key = os.environ['OPENAI_API_KEY']",
    "import ada.gates.gate as g; g.decide = lambda *a: None",
    "from improve import allowlist",
])
def test_tool_code_cannot_reach_protected_things(snippet):
    old = "x = 1\n"
    with pytest.raises(ChangeRejected):
        check_change("agents/tools/files.py", old, old + snippet + "\n")


def test_search_replace_must_match_once():
    files = {"prompts/eda.md": "a\nb\nb\n"}
    with pytest.raises(ChangeRejected):
        apply_change({"path": "prompts/eda.md", "kind": "search_replace", "search": "b", "replace": "c"}, files.get)
    p, old, new = apply_change({"path": "prompts/eda.md", "kind": "search_replace", "search": "a", "replace": "z"}, files.get)
    assert new == "z\nb\nb\n"

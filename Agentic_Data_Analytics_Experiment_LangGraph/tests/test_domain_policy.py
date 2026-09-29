"""Domain deny list: enforced in code for every network tool (before the call and after redirects)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from ada.config import CONFIG_PATH, Config
from ada.domains import DomainDenied, check_url, denied_rule, preferred
from agents.base import Agent
from agents.tools import REGISTRY, ToolContext
from improve.allowlist import ChangeRejected, check_change

CFG = Config.load()


@pytest.mark.parametrize("url", ["https://www.homegate.ch/mieten/wohnung", "http://homegate.ch", "https://api.homegate.ch/x",
                                 "https://www.immoscout24.ch/de/immobilien/mieten", "HTTPS://WWW.HOMEGATE.CH/"])
def test_denied_hosts_and_subdomains(url):
    assert denied_rule(CFG, url)
    with pytest.raises(DomainDenied):
        check_url(CFG, url)


@pytest.mark.parametrize("url", ["https://flatfox.ch/api/v1/public-listing/", "https://nothomegate.ch/",
                                 "https://homegate.ch.evil.example/", "https://github.com/Unco3892/SRED_2022"])
def test_other_hosts_allowed(url):
    check_url(CFG, url)


def test_prefer_list_parsed():
    urls = [p["url"] for p in preferred(CFG)]
    assert any("flatfox.ch" in u for u in urls) and any("SRED_2022" in u for u in urls)


class _Probe(Agent):
    name = "Probe"
    tools = ["fetch_url", "download_file", "download_zip_member"]


def test_dispatcher_refuses_before_any_network_call(ctx, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("network must not be touched")
    monkeypatch.setattr(httpx, "get", boom)
    monkeypatch.setattr(httpx.Client, "send", boom)
    agent = _Probe(ctx, "collect_data")
    for name, args in [("fetch_url", {"url": "https://www.homegate.ch/"}),
                       ("download_file", {"url": "https://www.immoscout24.ch/x.csv", "filename": "x.csv",
                                          "source_name": "x", "license": "unknown"}),
                       ("download_zip_member", {"url": "https://homegate.ch/a.zip", "member": "m", "filename": "m",
                                                "source_name": "x", "license": "unknown"})]:
        out = agent._execute(name, json.dumps(args), _Probe.tools, 5000)
        assert "blocked by the domain policy" in out
    refusals = [e for e in ctx.events.events(ctx.run_id, types=["tool_result"]) if e["payload"].get("policy") == "domain_deny"]
    assert len(refusals) == 3


def test_redirect_to_denied_host_is_blocked(ctx, monkeypatch):
    import agents.tools.web as web
    monkeypatch.setattr(web, "_robots_allowed", lambda tc, url: True)

    def fake_send(self, request, stream=False):
        return httpx.Response(200, request=httpx.Request("GET", "https://www.homegate.ch/landing"), text="hi")
    monkeypatch.setattr(httpx.Client, "send", fake_send)
    tool = REGISTRY["fetch_url"]
    with pytest.raises(DomainDenied):
        tool.fn(ToolContext(ctx, "collect_data", "c"), tool.params(url="https://short.example/redirect"))


def test_search_results_on_deny_list_are_dropped(ctx):
    ctx._llm = SimpleNamespace(web_search=lambda q, **k: {"answer": "…", "sources": [
        {"url": "https://www.homegate.ch/mieten", "title": "hg"}, {"url": "https://flatfox.ch/de/", "title": "ff"}]})
    tool = REGISTRY["web_search"]
    out = tool.fn(ToolContext(ctx, "collect_data", "c"), tool.params(query="rent"))
    assert [s["url"] for s in out["sources"]] == ["https://flatfox.ch/de/"] and "deny list" in out["note"]


def test_improver_cannot_change_or_remove_the_policy():
    import yaml
    data = yaml.safe_load(CONFIG_PATH.read_text())
    data["domains"]["deny"] = []
    with pytest.raises(ChangeRejected):
        check_change("config.yaml", CONFIG_PATH.read_text(), yaml.safe_dump(data))
    with pytest.raises(ChangeRejected):
        check_change("ada/domains.py", "", "x = 1\n")
    old = "def f(tc, url):\n    check_url(tc.ctx.cfg, url)\n    return 1\n"
    with pytest.raises(ChangeRejected):
        check_change("agents/tools/web.py", old, "def f(tc, url):\n    return 1\n")

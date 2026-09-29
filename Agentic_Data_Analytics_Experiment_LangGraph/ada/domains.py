"""Domain rules from config.yaml `domains` (protected: the improver cannot edit this module or the config key).

deny:   hosts that must never be contacted (e.g. portals whose terms of use forbid automated access).
        A rule matches the host itself and every subdomain ("homegate.ch" also blocks www.homegate.ch).
        Enforced in code: agents/base.py checks every URL argument of a network tool before it runs, and
        the web tools check again after redirects.
prefer: sources the operator has cleared; shown to the data agents as leads (not for benchmark runs).
"""
from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlparse

from ada.config import Config


class DomainDenied(PermissionError):
    pass


def host_of(url: str) -> str:
    host = (urlparse(url if "://" in url else f"https://{url}").hostname or "").lower()
    return host.rstrip(".")


def _rule_host(rule: str) -> str:
    return host_of(rule.strip())


def denied_rule(cfg: Config, url: str) -> str | None:
    """The deny rule matching this URL's host, or None."""
    host = host_of(url)
    if not host:
        return None
    for rule in cfg.get("domains.deny", []) or []:
        rh = _rule_host(str(rule))
        if rh and (host == rh or host.endswith("." + rh)):
            return str(rule)
    return None


def check_url(cfg: Config, url: str) -> None:
    rule = denied_rule(cfg, url)
    if rule:
        raise DomainDenied(f"{host_of(url)} is blocked by the domain policy (domains.deny: {rule}) — its terms do "
                           "not allow automated access. Do not use this source; choose another one.")


def urls_in(value: Any) -> Iterable[str]:
    """All http(s) URLs inside a (nested) tool-argument structure."""
    if isinstance(value, str):
        if value.startswith(("http://", "https://")):
            yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from urls_in(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from urls_in(v)


def preferred(cfg: Config) -> list[dict[str, str]]:
    out = []
    for entry in cfg.get("domains.prefer", []) or []:
        if isinstance(entry, str):
            out.append({"url": entry, "note": ""})
        elif isinstance(entry, dict) and entry.get("url"):
            out.append({"url": str(entry["url"]), "note": str(entry.get("note", ""))})
    return out

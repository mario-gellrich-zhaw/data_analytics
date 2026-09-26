"""What every agent-written script shares, whether it's a scraper
(scraper.py) or a data-preparation script (prep_code.py): the static
whitelist check before it may run, and the runner that really runs it in a
separate, time-limited Python process whose only way to the web is
sandbox/scraper_kit.py.

The static check is a whitelist (allowed imports, no dynamic
import/eval/open, no underscore attributes, only the public API of the
sandbox kits). It keeps an LLM's code honest in a classroom demo; it is not
a hard security boundary against someone deliberately attacking it — don't
expose this app publicly.
"""

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

# The sandbox folder sits next to tools/, at the app root.
SANDBOX_DIR = Path(__file__).resolve().parent.parent / "sandbox"
OUTPUT_TAIL_CHARS = 2500

# Standard-library modules every agent-written script may import — no file,
# process or network access among them.
SAFE_STDLIB_MODULES = frozenset(
    {
        "collections",
        "dataclasses",
        "datetime",
        "functools",
        "html",
        "itertools",
        "json",
        "math",
        "re",
        "statistics",
        "string",
        "time",
        "typing",
        "unicodedata",
        "urllib.parse",
    }
)

FORBIDDEN_NAMES = {
    "__import__",
    "__builtins__",
    "breakpoint",
    "compile",
    "delattr",
    "eval",
    "exec",
    "getattr",
    "globals",
    "input",
    "locals",
    "open",
    "setattr",
    "vars",
}
# Environment variables passed through to the script's process — just
# enough to find Python and reach the network (proxies, CA bundle).
PASSTHROUGH_ENV = (
    "PATH",
    "LANG",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
)

# Runs the agent's script with the sandbox dir importable but not the app's
# own modules (-P: no script/cwd dir prepended; -E: PYTHONPATH etc. ignored).
# Installed packages stay reachable — scraper_kit itself needs requests.
_BOOTSTRAP = (
    "import runpy, sys; sys.path.insert(0, sys.argv[1]); "
    "runpy.run_path(sys.argv[2], run_name='__main__')"
)


class _CodeChecker:
    """One static check of agent-written code (see `check_code`): walks the
    syntax tree once per rule and collects every problem found."""

    def __init__(self, allowed_modules, kits, forbidden_attributes):
        self.allowed_modules = allowed_modules
        self.kits = kits
        self.forbidden_attributes = forbidden_attributes
        self.kit_aliases: dict[str, str] = {}  # local name -> kit module
        self.problems: list[str] = []

    def module_allowed(self, name: str) -> bool:
        """Whether `name` (or its top-level package) is on the whitelist."""
        return name in self.allowed_modules or name.split(".")[0] in self.allowed_modules

    def check_import(self, node: ast.Import):
        """`import x` — only whitelisted modules; remembers kit aliases."""
        for alias in node.names:
            if not self.module_allowed(alias.name):
                self.problems.append(f"line {node.lineno}: import of '{alias.name}' not allowed")
            elif alias.name in self.kits:
                self.kit_aliases[alias.asname or alias.name] = alias.name

    def check_import_from(self, node: ast.ImportFrom):
        """`from x import y` — only a kit's public API, whitelisted modules,
        and no forbidden names."""
        module = node.module or ""
        for alias in node.names:
            if module in self.kits:
                if alias.name not in self.kits[module]:
                    self.problems.append(
                        f"line {node.lineno}: {module}.{alias.name} is not part of its "
                        f"public API ({', '.join(sorted(self.kits[module]))})"
                    )
            elif not (
                self.module_allowed(module) or self.module_allowed(f"{module}.{alias.name}")
            ):
                self.problems.append(f"line {node.lineno}: import from '{module}' not allowed")
            elif self.forbidden_attributes(alias.name):
                self.problems.append(f"line {node.lineno}: '{alias.name}' is not allowed")

    def check_attribute(self, node: ast.Attribute):
        """No private/dunder attributes and no forbidden ones (e.g. file I/O)."""
        if node.attr.startswith("_"):
            self.problems.append(
                f"line {node.lineno}: private/dunder attribute '.{node.attr}' not allowed"
            )
        elif self.forbidden_attributes(node.attr):
            self.problems.append(
                f"line {node.lineno}: '.{node.attr}' is not allowed (files and the web "
                "only through the sandbox kits)"
            )

    def check_kit_usage(self, tree: ast.AST):
        """Only the public API of each kit (e.g. not scraper_kit.requests),
        and the module itself only ever as `kit.<name>` — never passed
        around or re-bound, which would hide the attribute access."""
        attribute_bases = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                attribute_bases.add(id(node.value))
                kit = self.kit_aliases.get(node.value.id)
                if kit and node.attr not in self.kits[kit]:
                    self.problems.append(
                        f"line {node.lineno}: {kit}.{node.attr} is not part of its public API "
                        f"({', '.join(sorted(self.kits[kit]))})"
                    )
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and node.id in self.kit_aliases
                and id(node) not in attribute_bases
            ):
                kit = self.kit_aliases[node.id]
                self.problems.append(
                    f"line {node.lineno}: use {kit} only as {kit}.<name>, "
                    "don't pass the module around"
                )

    def run(self, tree: ast.AST) -> list[str]:
        """Apply every rule to the parsed code."""
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                self.check_import(node)
            elif isinstance(node, ast.ImportFrom):
                self.check_import_from(node)
            elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
                self.problems.append(f"line {node.lineno}: '{node.id}' is not allowed")
            elif isinstance(node, ast.Attribute):
                self.check_attribute(node)
        self.check_kit_usage(tree)
        return self.problems


def check_code(
    code: str,
    allowed_modules: set[str],
    kits: dict[str, set[str]],
    forbidden_attributes: Callable[[str], bool] = lambda _: False,
) -> list[str]:
    """Static whitelist check of agent-written code. `kits` maps each
    sandbox kit module (e.g. scraper_kit) to its public API;
    `forbidden_attributes` flags extra attribute names (e.g. pandas' file
    readers/writers). Returns the problems found (empty list = OK to run)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError on line {exc.lineno}: {exc.msg}"]
    return _CodeChecker(allowed_modules, kits, forbidden_attributes).run(tree)


def save_script(code: str, path: Path, problems: list[str]) -> dict:
    """Save an agent-written script (whether its check passed or not, so a
    rejected version can still be read) and describe it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code, encoding="utf-8")
    return {
        "path": str(path),
        "lines": len(code.splitlines()),
        "check_passed": not problems,
        "problems": problems,
    }


def read_request_log(log_path: Path, start_line: int = 0) -> list[dict]:
    """The JSON-lines request log scraper_kit writes, from `start_line` on."""
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()[start_line:]
    return [json.loads(line) for line in lines if line.strip()]


def describe_request(entry: dict) -> str:
    """One logged request as a line of progress text."""
    if entry.get("blocked"):
        return f"{entry['url']} → blocked: {entry['blocked']}"
    if entry.get("kind") == "robots.txt":
        return f"{entry['url']} → HTTP {entry.get('status')}"
    return f"GET {entry['url']} → HTTP {entry.get('status')} ({entry.get('elapsed_ms')} ms)"


def base_env() -> dict:
    """The passthrough part of a script process's environment — no API
    keys or other secrets; callers add their kit's own settings."""
    return {k: os.environ[k] for k in PASSTHROUGH_ENV if k in os.environ}


def run_script(
    script_path: Path, work_dir: Path, env: dict, timeout_s: int, on_progress=None
) -> dict:
    """Really run one saved script in a separate, time-limited process,
    streaming each request scraper_kit logs (env SCRAPER_KIT_LOG) as
    progress, and return what actually happened: exit code, output tail,
    the request log."""

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    work_dir.mkdir(parents=True, exist_ok=True)
    log_path = Path(env["SCRAPER_KIT_LOG"])
    log_path.unlink(missing_ok=True)
    stdout_path = work_dir / "output.txt"

    report(f"Running {script_path.name} (time limit {timeout_s} s) ...")
    started = time.monotonic()
    timed_out = False
    seen = 0
    with open(stdout_path, "w", encoding="utf-8") as out_file:
        proc = subprocess.Popen(  # pylint: disable=consider-using-with
            [sys.executable, "-E", "-P", "-c", _BOOTSTRAP, str(SANDBOX_DIR), str(script_path)],
            cwd=work_dir,
            env=env,
            stdout=out_file,
            stderr=subprocess.STDOUT,
        )
        while proc.poll() is None:
            if time.monotonic() - started > timeout_s:
                proc.kill()
                timed_out = True
                break
            for entry in read_request_log(log_path, seen):
                report(describe_request(entry))
                seen += 1
            time.sleep(0.5)
        proc.wait()
    for entry in read_request_log(log_path, seen):
        report(describe_request(entry))

    # Missing only if the folder was removed mid-run (e.g. a second demo run
    # started and cleared data/) — report that instead of crashing the run.
    output = (
        stdout_path.read_text(encoding="utf-8", errors="replace")
        if stdout_path.exists()
        else "(the run's output file disappeared — was a second run started at the same time?)"
    )
    return {
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 1),
        "output_tail": output[-OUTPUT_TAIL_CHARS:],
        "requests": read_request_log(log_path),
    }

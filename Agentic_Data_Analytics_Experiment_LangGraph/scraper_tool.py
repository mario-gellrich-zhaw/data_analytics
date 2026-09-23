"""Tools that let the Data Analyst write its own scraper and really run it.

1. `write_scraper_code` — the agent hands in a complete Python script; it's
   statically checked (see `check_scraper_code`) and saved as
   data/scrapers/scraper_vN.py so students can read every version.
2. `run_scraper` — really runs one saved version in a separate Python
   process: time limit, a fresh working directory, and an environment
   stripped of every secret (no OPENAI_API_KEY). The script's only way to
   the web is sandbox/scraper_kit.py, which enforces robots.txt, the domain
   allowlist, delays, a request budget, and stop-at-first-block in code.

The static check is a whitelist (allowed imports, no dynamic
import/eval/open, no underscore attributes, only the public scraper_kit
API). It keeps an LLM's code honest in a classroom demo; it is not a hard
security boundary against someone deliberately attacking it — don't expose
this app publicly.
"""

import ast
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

from sandbox.scraper_kit import ALLOWED_DOMAINS, FIELDS

SANDBOX_DIR = Path(__file__).resolve().parent / "sandbox"
RUN_TIMEOUT_SECONDS = 180
MAX_REQUESTS_PER_RUN = 15
MAX_ROWS = 150
OUTPUT_TAIL_CHARS = 2500

ALLOWED_MODULES = {
    "scraper_kit",
    "bs4",
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
SCRAPER_KIT_PUBLIC = {
    "polite_get",
    "save_rows",
    "ScrapeBlocked",
    "Page",
    "FIELDS",
    "ALLOWED_DOMAINS",
    "MAX_REQUESTS",
    "MAX_ROWS",
}
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
# Environment variables passed through to the scraper process — just
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


def _module_allowed(name: str) -> bool:
    return name in ALLOWED_MODULES or name.split(".")[0] == "bs4"


def check_scraper_code(code: str) -> list[str]:
    """Static whitelist check of agent-written scraper code. Returns the
    problems found (empty list = OK to run)."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return [f"SyntaxError on line {exc.lineno}: {exc.msg}"]

    problems: list[str] = []
    kit_aliases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not _module_allowed(alias.name):
                    problems.append(f"line {node.lineno}: import of '{alias.name}' not allowed")
                elif alias.name == "scraper_kit":
                    kit_aliases.add(alias.asname or "scraper_kit")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                full = f"{module}.{alias.name}"
                if module == "scraper_kit":
                    if alias.name not in SCRAPER_KIT_PUBLIC:
                        problems.append(
                            f"line {node.lineno}: scraper_kit.{alias.name} is not part of its "
                            f"public API ({', '.join(sorted(SCRAPER_KIT_PUBLIC))})"
                        )
                elif not (_module_allowed(module) or _module_allowed(full)):
                    problems.append(f"line {node.lineno}: import from '{module}' not allowed")
        elif isinstance(node, ast.Name) and node.id in FORBIDDEN_NAMES:
            problems.append(f"line {node.lineno}: '{node.id}' is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            problems.append(f"line {node.lineno}: private/dunder attribute '.{node.attr}' not allowed")

    # Only the public API of scraper_kit (e.g. not scraper_kit.requests),
    # and the module itself only ever as `scraper_kit.<name>` — never
    # passed around or re-bound, which would hide the attribute access.
    attribute_bases = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            attribute_bases.add(id(node.value))
            if node.value.id in kit_aliases and node.attr not in SCRAPER_KIT_PUBLIC:
                problems.append(
                    f"line {node.lineno}: scraper_kit.{node.attr} is not part of its public API "
                    f"({', '.join(sorted(SCRAPER_KIT_PUBLIC))})"
                )
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id in kit_aliases and id(node) not in attribute_bases:
            problems.append(
                f"line {node.lineno}: use scraper_kit only as scraper_kit.<name>, "
                "don't pass the module around"
            )
    return problems


def save_scraper_code(code: str, scrapers_dir: Path, version: int) -> dict:
    """Statically check the code and save it as scraper_v{version}.py
    (saved either way, so a rejected version can still be read)."""
    scrapers_dir.mkdir(parents=True, exist_ok=True)
    path = scrapers_dir / f"scraper_v{version}.py"
    path.write_text(code, encoding="utf-8")
    problems = check_scraper_code(code)
    return {
        "version": version,
        "path": str(path),
        "lines": len(code.splitlines()),
        "check_passed": not problems,
        "problems": problems,
    }


def _read_request_log(log_path: Path, start_line: int = 0) -> list[dict]:
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").splitlines()[start_line:]
    return [json.loads(line) for line in lines if line.strip()]


def _describe_request(entry: dict) -> str:
    if entry.get("blocked"):
        return f"{entry['url']} → blocked: {entry['blocked']}"
    if entry.get("kind") == "robots.txt":
        return f"{entry['url']} → HTTP {entry.get('status')}"
    return f"GET {entry['url']} → HTTP {entry.get('status')} ({entry.get('elapsed_ms')} ms)"


def scraper_env(log_path: Path, out_path: Path) -> dict:
    """The scraper process's whole environment: a short passthrough list
    plus scraper_kit's settings — no API keys or other secrets."""
    env = {k: os.environ[k] for k in PASSTHROUGH_ENV if k in os.environ}
    env.update(
        {
            "SCRAPER_KIT_LOG": str(log_path),
            "SCRAPER_KIT_OUT": str(out_path),
            "SCRAPER_KIT_MAX_REQUESTS": str(MAX_REQUESTS_PER_RUN),
            "SCRAPER_KIT_MAX_ROWS": str(MAX_ROWS),
            "PYTHONIOENCODING": "utf-8",
        }
    )
    return env


def run_scraper_code(
    script_path: Path, work_dir: Path, out_path: Path, on_progress=None
) -> dict:
    """Really run one saved scraper version in a separate, time-limited
    process, streaming each logged request as progress, and return what
    actually happened (exit code, output tail, request log, rows saved)."""

    def report(stage: str):
        if on_progress:
            on_progress(stage)

    work_dir.mkdir(parents=True, exist_ok=True)
    log_path = work_dir / "requests_log.jsonl"
    log_path.unlink(missing_ok=True)
    out_path.unlink(missing_ok=True)
    stdout_path = work_dir / "output.txt"

    env = scraper_env(log_path, out_path)

    report(f"Running {script_path.name} (time limit {RUN_TIMEOUT_SECONDS} s) ...")
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
            if time.monotonic() - started > RUN_TIMEOUT_SECONDS:
                proc.kill()
                timed_out = True
                break
            for entry in _read_request_log(log_path, seen):
                report(_describe_request(entry))
                seen += 1
            time.sleep(0.5)
        proc.wait()
    for entry in _read_request_log(log_path, seen):
        report(_describe_request(entry))

    elapsed_s = round(time.monotonic() - started, 1)
    output = stdout_path.read_text(encoding="utf-8", errors="replace")
    requests_log = _read_request_log(log_path)

    rows_saved, columns, sample_rows = 0, [], []
    if out_path.exists():
        try:
            df = pd.read_csv(out_path)
            rows_saved = len(df)
            columns = list(df.columns)
            sample_rows = json.loads(df.head(5).to_json(orient="values"))
        except (pd.errors.EmptyDataError, pd.errors.ParserError):
            pass

    report(
        f"{script_path.name}: {'timed out' if timed_out else f'exit code {proc.returncode}'}, "
        f"{len([e for e in requests_log if e.get('kind') == 'page' and not e.get('blocked')])} "
        f"page(s) fetched, {rows_saved} row(s) saved, {elapsed_s} s."
    )
    return {
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "elapsed_seconds": elapsed_s,
        "output_tail": output[-OUTPUT_TAIL_CHARS:],
        "requests": requests_log,
        "rows_saved": rows_saved,
        "columns": columns,
        "sample_rows": sample_rows,
    }


# --- Tool schemas -------------------------------------------------------

WRITE_SCRAPER_CODE_SCHEMA = {
    "type": "function",
    "function": {
        "name": "write_scraper_code",
        "description": (
            "Write (or rewrite) your own complete Python scraper script. It is "
            "checked and saved as a new version; then call run_scraper to really "
            "run it. Rules, enforced in code: the script may import only "
            "scraper_kit, bs4, json, re, math, time, datetime, collections, "
            "itertools, functools, statistics, string, html, unicodedata, typing, "
            "dataclasses, urllib.parse — no requests/urllib.request/socket/os/"
            "subprocess, no open/eval/exec/getattr, no _private attributes. The ONLY "
            "way to the web is scraper_kit.polite_get(url, params=None) -> Page "
            "(.status_code, .text, .headers, .json()); it checks robots.txt, allows "
            f"only {', '.join(ALLOWED_DOMAINS)}, waits 2-5 s between "
            f"requests, allows at most {MAX_REQUESTS_PER_RUN} requests per run, and "
            "raises scraper_kit.ScrapeBlocked at the first 403/429/bot challenge "
            "(that site then stays blocked for the run — never try to get around "
            "it). Store results with scraper_kit.save_rows(list_of_dicts) using only "
            f"these keys: {', '.join(FIELDS)} (at most {MAX_ROWS} rows kept) — call "
            "it after EVERY page, not once at the end: rows already saved are kept "
            "even if the script stops or crashes later. "
            "Keep only rental apartments — listing sites also carry parking "
            "spaces, commercial units and properties for sale. "
            "print() anything useful — you'll "
            "see the output. Known entry points: immoscout24.ch search "
            "https://www.immoscout24.ch/de/wohnung/mieten/ort-zuerich?pn=1 ; "
            "homegate.ch search https://www.homegate.ch/mieten/wohnung/ort-zuerich/"
            "trefferliste ; flatfox.ch public JSON API "
            "https://flatfox.ch/api/v1/public-listing/ (paginated with limit (max "
            "100) and offset, returns {count, next, results: [...]}, all of "
            "Switzerland, no server-side location filter or sorting; results are "
            "oldest first and the first pages contain very few Zurich listings, so "
            "spread your offsets across the whole range up to count). Catch ScrapeBlocked per "
            "site so one blocked site doesn't stop the whole script."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "The complete Python script (not a diff).",
                }
            },
            "required": ["code"],
        },
    },
}

RUN_SCRAPER_SCHEMA = {
    "type": "function",
    "function": {
        "name": "run_scraper",
        "description": (
            "Really run a scraper version you wrote with write_scraper_code, in a "
            f"separate process (time limit {RUN_TIMEOUT_SECONDS} s). Returns the "
            "real exit code, the tail of its printed output (incl. any traceback), "
            "every request it made with its real HTTP status or block reason, the "
            "real structure of the first page fetched per site (response_structure: "
            "JSON keys, or the HTML title) and how many rows it saved plus a sample. "
            "If it failed, read the error and response_structure — use the real key "
            "names shown there, don't guess — fix the code with write_scraper_code, "
            "and run again."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "version": {
                    "type": "integer",
                    "description": "Which version to run; omit for the latest.",
                }
            },
        },
    },
}

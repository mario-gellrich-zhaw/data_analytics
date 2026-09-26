"""Help for an agent that's stuck: what earlier runs of this demo did at
the same step, read from their saved Markdown transcripts
(conversation_history/*.md, see reporting/transcript.py).

For one stage — the scraper, the cleaning or the enrichment script — it
returns, newest run first, the script version that finally worked and the
problems earlier runs ran into (crashes, rejections), so the agent can
learn from them instead of guessing again. RunTools only allows it once an
agent really is stuck (see teaching.TeachingAids.is_stuck); from the start, every run
must find its own way.
"""

import re
from pathlib import Path

STAGE_PREFIXES = {"scraper": "scraper_v", "clean": "clean_v", "enrich": "enrich_v"}
MAX_RUNS = 4  # newest saved runs searched
MAX_SCRIPTS = 2  # accepted scripts returned
MAX_PROBLEMS = 6
MAX_CODE_CHARS = 6000

# "**Data Analyst wrote `scraper_v3.py`** (...)" or
# "**enrich_v2.py — enrichment code written by the Data Analyst (...)**"
_WROTE_RE = re.compile(r"^\*\*(?:.*?`)?((?:scraper|clean|enrich)_v\d+\.py)")
_RAN_RE = re.compile(r"^\*\*Ran `((?:scraper|clean|enrich)_v\d+\.py)`:\*\*\s*(.*)")


def _run_block(lines: list[str], start: int) -> dict:
    """A run's verdict ("- Accepted ..." / "- Not accepted: ...") and the
    last line of its printed output (e.g. a traceback's error), read up to
    the next bold header."""
    verdict, printed, in_output = "", [], False
    for line in lines[start:]:
        if line.startswith("**") or line.startswith("## "):
            break
        if line.startswith("- Accepted") or line.startswith("- Not accepted"):
            verdict = line[2:]
        elif line.startswith("Printed output"):
            in_output = True
        elif in_output and not line.startswith("```") and line.strip():
            printed.append(line.strip())
    return {"verdict": verdict, "last_output": printed[-1] if printed else ""}


def _parse_transcript(text: str) -> tuple[dict[str, str], list[dict]]:
    """Every script's code by file name, and every run in order with its
    outcome line and verdict."""
    codes: dict[str, str] = {}
    runs: list[dict] = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        wrote = _WROTE_RE.match(line)
        ran = _RAN_RE.match(line)
        if ran:
            runs.append({"file": ran.group(1), "outcome": ran.group(2), **_run_block(lines, i + 1)})
        elif wrote and i + 1 < len(lines) and lines[i + 1].startswith("```"):
            end = next(
                (j for j in range(i + 2, len(lines)) if lines[j].startswith("```")), len(lines)
            )
            codes[wrote.group(1)] = "\n".join(lines[i + 2 : end])
            i = end
        i += 1
    return codes, runs


def _stage_in_run(path: Path, prefix: str) -> tuple[dict | None, list[str]]:
    """One saved run: the last accepted script of the stage (if any) and
    every problem its runs of that stage hit."""
    run_label = path.stem.replace("conversation_", "run ")
    codes, runs = _parse_transcript(path.read_text(encoding="utf-8"))
    stage_runs = [r for r in runs if r["file"].startswith(prefix)]
    problems = []
    for run in stage_runs:
        if not run["verdict"].startswith("Accepted"):
            detail = " — ".join(x for x in (run["verdict"], run["last_output"]) if x)
            problems.append(f"{run_label}, {run['file']}: {run['outcome']} {detail}")
    good = [r for r in stage_runs if r["verdict"].startswith("Accepted") and r["file"] in codes]
    if not good:
        return None, problems
    return {"run": run_label, "file": good[-1]["file"], "code": codes[good[-1]["file"]]}, problems


def look_up_past_runs(history_dir: Path, stage: str, keyword: str = "") -> dict:
    """What earlier runs did at `stage`: the scripts that were finally
    accepted (newest first) and the problems runs hit on the way. With a
    `keyword`, only scripts/problems mentioning it."""
    prefix = STAGE_PREFIXES.get(stage)
    if not prefix:
        return {"error": f"unknown stage '{stage}' — use one of {', '.join(STAGE_PREFIXES)}"}
    files = sorted(history_dir.glob("conversation_*.md"), reverse=True)[:MAX_RUNS]
    accepted, problems = [], []
    for path in files:
        script, run_problems = _stage_in_run(path, prefix)
        problems += run_problems
        if script:
            accepted.append(script)
    if keyword:
        needle = keyword.lower()
        accepted = [a for a in accepted if needle in a["code"].lower()]
        problems = [p for p in problems if needle in p.lower()]
    for item in accepted:
        if len(item["code"]) > MAX_CODE_CHARS:
            item["code"] = item["code"][:MAX_CODE_CHARS] + "\n# … (cut)"
    return {
        "stage": stage,
        "runs_searched": len(files),
        "accepted_scripts": accepted[:MAX_SCRIPTS],
        "past_problems": problems[:MAX_PROBLEMS],
        "note": (
            "These are earlier runs' real scripts and problems — learn from them, "
            "but check them against THIS run's real data and responses."
        ),
    }

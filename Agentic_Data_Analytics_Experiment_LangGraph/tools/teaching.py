"""The teaching aids on top of a run's real tools (see run_tools.py):

- `show_to_class` puts a real example in front of the students — a few
  real rows, ONE listing before/after preparation, or a few real lines of a
  script (built in exhibits.py, always from the actual files);
- `look_up_past_runs` lets a coding agent that's stuck see how earlier runs
  of this demo solved the same step (history.py) — refused until it really
  is stuck, so every run first has to find its own way.
"""

from pathlib import Path

from agents.graph import TEAM_NOTE_KEY
from tools.exhibits import code_exhibit, rows_exhibit, single_case_exhibit
from tools.history import look_up_past_runs

MAX_EXHIBITS_PER_PHASE = 4  # a few concrete examples, not a slideshow


class TeachingAids:
    """show_to_class and look_up_past_runs for one run. Reads the run's
    state (current dataset, scripts written, runs so far) from `tools`, a
    RunTools; `budgets` are its run limits per stage."""

    def __init__(self, tools, budgets: dict[str, int]):
        self.tools = tools
        self.budgets = budgets  # "scraper" / "prep" -> max runs
        self.exhibits_shown = 0  # in the current phase

    def new_phase(self):
        """A new phase starts: its own budget of exhibits."""
        self.exhibits_shown = 0

    def _script_path(self, name: str) -> str:
        """The saved path of a script this run wrote, by file name."""
        prep = self.tools.prep
        written = [*self.tools.scraper_versions, *prep.versions["clean"], *prep.versions["enrich"]]
        return next((w["path"] for w in written if Path(w["path"]).name == name), "")

    def _data_exhibit(self, kind: str, args: dict) -> dict:
        """A rows / single_case exhibit of the current dataset."""
        current = self.tools.current_file
        if kind == "rows":
            return rows_exhibit(current["path"], current["format"], args.get("columns"),
                                args.get("n") or 5, args.get("listing_ids"))
        if args.get("listing_id") is None:
            return {"error": "single_case needs a listing_id"}
        collected = self.tools.prep.collected
        before = collected if collected["path"] else current
        return single_case_exhibit(before["path"], before["format"], current["path"],
                                   args["listing_id"], args.get("columns"))

    def _build_exhibit(self, kind: str, args: dict) -> dict:
        """The real exhibit behind a show_to_class call (see exhibits.py)."""
        if kind == "code":
            path = self._script_path(args.get("script") or "")
            if not path:
                return {"error": f"no script called '{args.get('script')}' was written this run"}
            return code_exhibit(path, args.get("start_line"), args.get("end_line"))
        if kind not in ("rows", "single_case"):
            return {"error": f"unknown kind '{kind}' — use rows, single_case or code"}
        if self.tools.no_file_error():
            return {"error": "No dataset exists yet, so there's nothing to show."}
        return self._data_exhibit(kind, args)

    def call_show_to_class(self, kind: str, caption: str = "", **args):
        """Tool: put a real example in front of the class — a few rows, one
        listing before/after preparation, or a few lines of a script."""
        if self.exhibits_shown >= MAX_EXHIBITS_PER_PHASE:
            return {"shown": False, "error": "Enough examples for this step — keep going."}
        if not caption.strip():
            # An example without a word on what to notice teaches little —
            # live runs showed code with an empty caption.
            return {"shown": False, "error": "Add a caption: one sentence on what to notice."}
        try:
            exhibit = self._build_exhibit(kind, args)
        except (OSError, ValueError, KeyError) as exc:
            exhibit = {"error": str(exc)}
        if exhibit.get("error"):
            return {"shown": False, "error": exhibit["error"]}
        self.exhibits_shown += 1
        exhibit["caption"] = caption
        self.tools.on_artifact("exhibit", exhibit)
        what = {
            "rows": f"{len(exhibit.get('rows', []))} real rows of {exhibit.get('file')}",
            "single_case": f"listing {exhibit.get('listing_id')} before/after preparation",
            "code": f"lines {exhibit.get('start_line')}+ of {exhibit.get('file')}",
        }[kind]
        return {"shown": True, TEAM_NOTE_KEY: f"Shown to the class: {what} — {caption}"}

    def is_stuck(self, stage: str) -> bool:
        """Whether the coder of `stage` can't get further on its own: its
        last two runs both failed, or its run budget is nearly used up with
        nothing accepted. Only then may it look at earlier runs."""
        if stage == "scraper":
            runs = [bool(r.get("accepted_as_dataset")) for r in self.tools.scraper_runs]
            budget = self.budgets["scraper"]
        else:
            runs = [bool(r.get("accepted")) for r in self.tools.prep.runs.get(stage, [])]
            budget = self.budgets["prep"]
        last_two_failed = len(runs) >= 2 and not any(runs[-2:])
        budget_nearly_gone = len(runs) >= budget - 2 and not any(runs)
        return last_two_failed or budget_nearly_gone

    def call_look_up_past_runs(self, stage: str = "", keyword: str = ""):
        """Tool: what earlier runs of this demo did at this step — only
        once the agent is stuck (see is_stuck)."""
        stage = stage or self.tools.prep.stage or "scraper"
        if not self.is_stuck(stage):
            return {
                "error": (
                    "Not available yet: first find your own way. This opens up only once "
                    "you're stuck (your last two runs in this step failed)."
                )
            }
        result = look_up_past_runs(self.tools.paths.history, stage, keyword)
        scripts = result.get("accepted_scripts", [])
        problems = result.get("past_problems", [])
        self.tools.on_artifact(
            "history_lookup",
            {
                "stage": stage,
                "keyword": keyword,
                "runs_searched": result.get("runs_searched", 0),
                "found": [f"{a['file']} ({a['run']})" for a in scripts],
                "problems": problems,
            },
        )
        result[TEAM_NOTE_KEY] = (
            f"Stuck at the {stage} step, so earlier runs of this demo were consulted: "
            f"{len(scripts)} working script(s) and {len(problems)} past problem(s) found."
        )
        return result

"""Per-run wrappers around the pure tool functions in opendata.py,
preparation.py, scraper.py and prep_code.py.

Those functions are stateless; a live run needs a bit of shared state on
top of them — which file is "current" right now, the real results captured
so each phase's result card can be built, and short ids handed to the
model in place of long opendata.swiss resource URLs. `RunTools` bundles
that state with the actual tool-calling wrappers the agents invoke, one
fresh instance per run.
"""

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NamedTuple

from agents.graph import TEAM_NOTE_KEY
from tools.opendata import discard_dataset, download_dataset, search_open_data
from tools.prep_code import judge_prep_output, run_prep_code, save_prep_code
from tools.preparation import (
    make_sketch,
    preview_data,
    profile_data,
    run_sql_query,
    store_to_database,
)
from tools.scraper import MAX_REQUESTS_PER_RUN, MAX_ROWS, run_scraper_code, save_scraper_code
from tools.teaching import TeachingAids
from tools.validation import check_scraped_fields, filled_share, looks_like_listing_data

MAX_SCRAPER_RUNS = 6  # per demo run — each scraper run may make up to 15 requests
MAX_PREP_RUNS = 6  # per preparation stage (cleaning, enrichment)
# The UI shows 10 sample rows; the model needs fewer (they're long).
AGENT_SAMPLE_ROWS = 5
PREP_STAGE_LABELS = {"clean": "cleaning", "enrich": "enrichment"}
STUCK_HINT = (
    "- You seem stuck (your last runs in this step all failed). Only now you may call "
    "look_up_past_runs(stage='{stage}') to see how earlier runs of this demo solved this "
    "step and which problems they hit — then adapt it to THIS run's real data."
)

PARSING_BUG_DIAGNOSIS = (
    "The site itself answered fine (HTTP 200) — this is a bug in YOUR code, "
    "not a problem with the source, so don't give up on it: compare the key "
    "names your code reads with the real ones in response_structure (see the "
    "first_item sample and its values), fix the mapping with "
    "write_scraper_code, and run again."
)


def _parsing_failed(run: dict) -> bool:
    """Whether a run that got real pages still failed to turn them into
    usable rows: it crashed, its rows were rejected, or it saved nothing at
    all. A clean exit with 0 rows is almost always field names or filters
    that don't match the real response (e.g. reading `zip` when the API
    says `zipcode` silently filters out every listing) — not an empty
    source."""
    return bool(run["exit_code"] != 0 or run.get("rejected_because") or run["rows_saved"] == 0)


def _failed_requests_diagnosis(requests_log: list[dict]) -> str:
    """For a prep run whose web lookups (partly) failed: say so plainly,
    with the first real reason. Without it, a run with failed lookups but
    accepted derived columns read as a success, and the agent put the fix
    off to "future runs"."""
    failed = [e for e in requests_log if e.get("blocked")]
    if not failed:
        return ""
    return (
        f"{len(failed)} of {len(requests_log)} web requests failed — first reason: "
        f"{failed[0]['blocked']}. Whatever those lookups were for is missing from the "
        "output: fix the request (the reason above is the server's own answer) and run "
        "again — don't leave it for later."
    )


def _new_column_fill(result: dict) -> dict[str, float]:
    """Share of rows each column the run added is filled in."""
    rows = result["rows_after"] or 1
    missing = result["missing_values"]
    return {col: 1 - missing.get(col, 0) / rows for col in result["added_columns"]}


def _sparse_columns_diagnosis(result: dict) -> str:
    """For a run whose new columns are mostly empty (e.g. a lookup tried on
    a small sample first): say so. A live run was declared done with a
    municipality column 92% empty and nobody noticed."""
    sparse = {c: share for c, share in _new_column_fill(result).items() if share < 0.5}
    if not sparse:
        return ""
    detail = ", ".join(f"{c} ({share:.0%} filled)" for c, share in sparse.items())
    rows = result["rows_after"] or 1
    before = result.get("input_missing_values") or {}
    thin_inputs = ", ".join(
        f"{c} ({1 - n / rows:.0%} filled)" for c, n in before.items() if n / rows > 0.5
    )
    hint = (
        f" The input's own sparse columns — {thin_inputs} — cap any feature computed "
        "from them; derive it from a better-filled source (e.g. the listing text) or "
        "say plainly how full it is." if thin_inputs else ""
    )
    return (
        f"New column(s) mostly empty: {detail}. If that was a test on a few rows, run it "
        f"on all rows now; if the source really has no value, say why.{hint}"
    )


def _quality_diagnosis(result: dict) -> str:
    """For a cleaning run whose output still has data-quality issues (see
    validation.data_quality_issues): name them, so the cleaner and its
    reviewer look at them instead of declaring the data clean."""
    if result.get("stage") != "clean" or not result.get("quality_issues"):
        return ""
    return (
        "Still in the data after this run: " + "; ".join(result["quality_issues"])
        + ". Look at those listings (their text often has the right value) and fix, "
        "null, merge or drop them — or say why they're genuine."
    )


# An accepted scrape below this share of the row cap is a thin basis for a
# price model — a live run stopped at 52 listings with two thirds of its
# request budget unused.
SMALL_SAMPLE_SHARE = 0.6


def _small_sample_hint(run: dict) -> str:
    """For an accepted scrape with few rows and request budget left: say
    that more pages are there for the taking."""
    left = MAX_REQUESTS_PER_RUN - sum(1 for e in run["requests"] if e.get("kind") == "page")
    if not run.get("accepted_as_dataset") or run["rows_saved"] >= SMALL_SAMPLE_SHARE * MAX_ROWS:
        return ""
    if left <= 0:
        return ""
    return (
        f"Accepted, but only {run['rows_saved']} listings while {left} of this run's "
        f"{MAX_REQUESTS_PER_RUN} requests were left unused — a price model learns far more "
        f"from a bigger sample (up to {MAX_ROWS} rows are kept): fetch more pages in one "
        "improved scraper version and run it once more."
    )


def _page_hosts(run: dict) -> list[str]:
    """The sites a scraper run really got pages from (e.g. flatfox.ch) —
    named in the team note, since a live run's agents called Flatfox data
    "the Homegate dataset"."""
    return sorted(
        {
            e["url"].split("/")[2]
            for e in run["requests"]
            if e.get("kind") == "page" and e.get("status") == 200
        }
    )


def _compact_requests(requests_log: list[dict]) -> list[dict]:
    """A request log small enough for the model: each URL with its status
    or block reason. A long run of lookups is cut to its first 10 plus any
    blocked ones."""
    compact = [
        {k: e.get(k) for k in ("url", "status", "blocked") if e.get(k) is not None}
        for e in requests_log
    ]
    if len(compact) <= 12:
        return compact
    blocked = [e for e in compact[10:] if e.get("blocked")]
    return compact[:10] + blocked + [{"note": f"{len(compact)} requests in total"}]


def _run_note(script: str, run: dict, facts: str, verdict: str) -> str:
    """The factual line every agent sees in the shared conversation after a
    script really ran (see agents/graph.py's TEAM_NOTE_KEY)."""
    outcome = "timed out" if run["timed_out"] else f"exit code {run['exit_code']}"
    note = f"Real run of {script}: {outcome}, {facts} — {verdict}."
    printed = run["output_tail"].strip()[-400:]
    return f"{note} Last printed output:\n{printed}" if printed else note


def _lost_columns_diagnosis(result: dict) -> str:
    """For an accepted run that no longer produces columns an earlier
    accepted run of the same stage added: every run starts again from the
    stage's input, so those are gone. A live run lost nine amenity flags
    this way when the agent rewrote its script for one lookup."""
    lost = result.get("lost_columns") or []
    if not lost:
        return ""
    return (
        f"This run no longer produces {', '.join(lost)}, which an earlier accepted run "
        "added — every run starts again from the stage's input, so the LAST accepted "
        "script is the whole step. Put the earlier features back into this script "
        "(unless dropping them is intended) and run again."
    )


def _prep_agent_view(result: dict) -> dict:
    """What the model gets back from run_prep_code: the result without the
    full request log (a compact one instead), the real response structure
    of any lookup, a diagnosis of failed lookups, and the team note."""
    view = {k: v for k, v in result.items() if k not in ("requests", "dtypes")}
    view["dtypes_after"] = result["dtypes"]
    view["sample_rows"] = result["sample_rows"][:AGENT_SAMPLE_ROWS]
    structure = [{"url": e["url"], **e["shape"]} for e in result["requests"] if e.get("shape")]
    if structure:
        view["response_structure"] = structure
    view["requests"] = _compact_requests(result["requests"])
    diagnosis = " ".join(
        d for d in (_failed_requests_diagnosis(result["requests"]),
                    _sparse_columns_diagnosis(result),
                    _lost_columns_diagnosis(result),
                    _quality_diagnosis(result)) if d
    )
    if diagnosis:
        view["diagnosis"] = diagnosis
    failed = sum(1 for e in result["requests"] if e.get("blocked"))
    added = ", ".join(
        f"{col} ({share:.0%} filled)" for col, share in _new_column_fill(result).items()
    ) or "none"
    view[TEAM_NOTE_KEY] = _run_note(
        f"{result['stage']}_v{result['version']}.py",
        result,
        f"{result['rows_before']} → {result['rows_after']} rows, new columns: {added}, "
        f"{len(result['requests'])} web requests ({failed} failed)",
        "ACCEPTED as the current dataset"
        if result["accepted"]
        else f"REJECTED ({result['rejected_because']})",
    )
    if result.get("lost_columns"):
        view[TEAM_NOTE_KEY] += (
            f" ⚠ It no longer contains {', '.join(result['lost_columns'])} from the "
            "earlier accepted run — the last accepted script is the whole step."
        )
    if _quality_diagnosis(result):
        view[TEAM_NOTE_KEY] += f" ⚠ {_quality_diagnosis(result)}"
    if result.get("walkthrough_note"):
        view[TEAM_NOTE_KEY] += (
            " The app showed the class how this step derived its columns, e.g. "
            f"{result['walkthrough_note']}."
        )
    return view


@dataclass
class PrepState:
    """Step 4's preparation stages: which one is running ("clean" /
    "enrich", set by app/demo_run.py), the file its scripts read, and every
    script version/run per stage."""

    stage: str = ""
    input: dict = field(default_factory=lambda: {"path": "", "format": ""})
    # the dataset as collected in Step 3 — the "before" of a single case
    collected: dict = field(default_factory=lambda: {"path": "", "format": ""})
    versions: dict = field(default_factory=lambda: {"clean": [], "enrich": []})
    runs: dict = field(default_factory=lambda: {"clean": [], "enrich": []})


class DataPaths(NamedTuple):
    """The real files/folders one run's tools read from / write to."""

    download: Path
    scraped: Path  # the accepted output of the agents' own scraper
    scrapers_dir: Path  # every scraper version the agents wrote, plus each run's log
    cleaned: Path
    db: Path
    prep_dir: Path  # every cleaning/enrichment script version, plus each run's log
    enriched: Path
    history: Path  # earlier runs' saved transcripts (look_up_past_runs)


class RunTools:
    """Real-tool state and call wrappers for one run.

    `results` holds the real, latest result of each tool a phase's result
    card needs to show (see app/demo_run.py's step3_result/step4_result); the
    other attributes track which file preview/profile/prepare/store act on
    right now.
    """

    def __init__(self, paths: DataPaths, on_progress: Callable, on_artifact: Callable):
        self.paths = paths
        self.on_progress = on_progress
        # (kind, data) -> None: shows a scraper version / run inline in the
        # chat the moment it happens (see app/demo_run.py's _on_artifact).
        self.on_artifact = on_artifact
        self.scraper_versions: list[dict] = []  # every write_scraper_code call, in order
        self.scraper_runs: list[dict] = []  # every run_scraper call, in order
        self.prep = PrepState()
        self.results: dict[str, dict] = {
            "opendata": {},
            "download": {},
            "download_preview": {},  # first 10 raw rows, captured right after download
            "profile": {},  # the collected dataset, profiled as Step 4 opens
            "clean": {},  # the accepted cleaning run's summary
            "enrich": {},  # the accepted enrichment run's summary
            "prepared_preview": {},  # first 10 rows after the latest accepted stage
            "store": {},
            "sql": {},
            "sketch": {},
        }
        self.resource_lookup: dict[str, dict] = {}  # short id -> real resource info
        self.current_file = {"path": str(paths.download), "format": "CSV"}
        self.dataset_ready = False  # True only while current_file is a real, undiscarded download
        # show_to_class / look_up_past_runs (see teaching.py)
        self.teaching = TeachingAids(
            self, budgets={"scraper": MAX_SCRAPER_RUNS, "prep": MAX_PREP_RUNS}
        )

    def build_tool_impls(self) -> dict[str, Callable]:
        """Name -> callable, for the graph's tool-execution step. Safe to
        share across every agent: a model can only ever request a tool
        that was actually bound to it for that call (see agents/personas.py)."""
        return {
            "write_scraper_code": self.call_write_scraper_code,
            "run_scraper": self.call_run_scraper,
            "search_open_data": self.call_search_open_data,
            "download_dataset": self.call_download_dataset,
            "discard_dataset": self.call_discard_dataset,
            "preview_data": self.call_preview_data,
            "profile_data": self.call_profile_data,
            "write_prep_code": self.call_write_prep_code,
            "run_prep_code": self.call_run_prep_code,
            "store_to_database": self.call_store_to_database,
            "run_sql_query": self.call_run_sql_query,
            "make_sketch": self.call_make_sketch,
            "show_to_class": self.teaching.call_show_to_class,
            "look_up_past_runs": self.teaching.call_look_up_past_runs,
        }

    def capture_preview(self, result_key: str, path: str, data_format: str):
        """A real preview of the first 10 rows, captured automatically the
        moment data becomes available (after download, after cleaning) —
        shown in the UI regardless of whether an agent happens to call
        preview_data itself."""
        try:
            self.results[result_key] = preview_data(path=path, data_format=data_format, n=10)
        except Exception:  # pylint: disable=broad-exception-caught
            pass  # best-effort only; never break the run over a preview

    # --- Data Analyst tools: Collecting data --------------------------

    def call_write_scraper_code(self, code: str):
        """Tool: save (and statically check) a new version of the agent's
        own scraper script. Every version is kept for the UI/transcript."""
        version = len(self.scraper_versions) + 1
        result = save_scraper_code(code, self.paths.scrapers_dir, version)
        self.scraper_versions.append({**result, "code": code})
        self.on_artifact("scraper_code", {**result, "code": code})
        self.on_progress(
            f"scraper_v{version}.py written ({result['lines']} lines) — "
            + ("check passed." if result["check_passed"] else "check FAILED.")
        )
        return result

    def call_run_scraper(self, version: int | None = None):
        """Tool: really run one saved scraper version (see scraper.py).
        If it saved enough rows that look like listings, its CSV becomes
        the current dataset — exactly like an accepted download."""
        if not self.scraper_versions:
            return {
                "success": False,
                "error": "No scraper written yet — call write_scraper_code first.",
            }
        if len(self.scraper_runs) >= MAX_SCRAPER_RUNS:
            return {
                "success": False,
                "error": (
                    f"Scraper run budget used up ({MAX_SCRAPER_RUNS} runs). "
                    "Work with what you have or use open data."
                ),
            }
        version = version or len(self.scraper_versions)
        if not 1 <= version <= len(self.scraper_versions):
            return {"success": False, "error": f"Unknown version {version}."}
        written = self.scraper_versions[version - 1]
        if not written["check_passed"]:
            return {
                "success": False,
                "error": "That version failed the code check — fix it first.",
                "problems": written["problems"],
            }

        work_dir = self.paths.scrapers_dir / f"run_{len(self.scraper_runs) + 1}_v{version}"
        out_path = work_dir / "scraped_listings.csv"
        result = run_scraper_code(
            Path(written["path"]), work_dir, out_path, on_progress=self.on_progress
        )
        result["version"] = version
        result["success"] = result["exit_code"] == 0 and not result["timed_out"]

        if result["rows_saved"] > 0:
            result["filled_share"] = filled_share(out_path)
            looks_ok, reason = check_scraped_fields(result["filled_share"])
            if looks_ok:
                looks_ok, reason = looks_like_listing_data(str(out_path), "CSV")
            if looks_ok:
                shutil.copyfile(out_path, self.paths.scraped)
                self.current_file["path"] = str(self.paths.scraped)
                self.current_file["format"] = "CSV"
                self.dataset_ready = True
                self.results["download"] = self._scraped_download_result(result)
                self.capture_preview("download_preview", str(self.paths.scraped), "CSV")
                result["accepted_as_dataset"] = True
            else:
                result["accepted_as_dataset"] = False
                result["rejected_because"] = reason
        self.scraper_runs.append(result)
        self.on_artifact("scraper_run", result)

        # The model gets the real structure first (it's what to parse
        # against) and only a compact request log; the UI gets it all.
        structure = [{"url": e["url"], **e["shape"]} for e in result["requests"] if e.get("shape")]
        agent_view = {"response_structure": structure}
        if structure and _parsing_failed(result):
            agent_view["diagnosis"] = PARSING_BUG_DIAGNOSIS
        elif _small_sample_hint(result):
            agent_view["diagnosis"] = _small_sample_hint(result)
        agent_view.update({k: v for k, v in result.items() if k != "requests"})
        agent_view["sample_rows"] = result["sample_rows"][:AGENT_SAMPLE_ROWS]
        agent_view["requests"] = _compact_requests(result["requests"])
        hosts = _page_hosts(result)
        source = f" from {', '.join(hosts)}" if hosts else ""
        agent_view[TEAM_NOTE_KEY] = _run_note(
            f"scraper_v{version}.py", result, f"{result['rows_saved']} rows saved{source}",
            "ACCEPTED as the dataset" if result.get("accepted_as_dataset")
            else f"not accepted ({result.get('rejected_because') or 'no rows'})",
        )
        return agent_view

    def scraper_working_notes(self) -> str:
        """The Data Analyst's private memory of its latest scraper run —
        tool results only live for the turn that called the tool, so
        without this a fix written on a later turn would have to guess the
        real field names and error all over again."""
        if not self.scraper_runs:
            return ""
        run = self.scraper_runs[-1]
        code = self.scraper_versions[run["version"] - 1]["code"]
        structure = [{"url": e["url"], **e["shape"]} for e in run["requests"] if e.get("shape")]
        if run.get("accepted_as_dataset"):
            verdict = "ACCEPTED as the current dataset"
        elif run.get("rejected_because"):
            verdict = f"REJECTED: {run['rejected_because']}"
        else:
            verdict = "no dataset produced"
        if structure and _parsing_failed(run):
            verdict += f" — {PARSING_BUG_DIAGNOSIS}"
        elif _small_sample_hint(run):
            verdict += f" — {_small_sample_hint(run)}"
        lines = [
            "Your private working notes (only you see these) from your LAST scraper run — "
            f"scraper_v{run['version']}.py "
            f"({len(self.scraper_runs)}/{MAX_SCRAPER_RUNS} runs used):",
            f"- exit code {run['exit_code']}{' (timed out)' if run['timed_out'] else ''}, "
            f"{run['rows_saved']} rows saved, {verdict}",
            "- requests: "
            + "; ".join(
                f"{e['url']} -> {e.get('blocked') or e.get('status')}" for e in run["requests"]
            ),
            f"- last printed output / traceback:\n{run['output_tail'][-800:]}",
        ]
        if structure:
            lines.append(
                "- REAL response structure (use exactly these key names):\n"
                + json.dumps(structure, ensure_ascii=False)
            )
        lines.append(f"- the code you ran:\n{code}")
        if self.teaching.is_stuck("scraper"):
            lines.append(STUCK_HINT.format(stage="scraper"))
        return "\n".join(lines)

    def _scraped_download_result(self, run: dict) -> dict:
        """Shape an accepted scraper run like a download result, so the
        Step 3 card and Step 4's tools treat it the same way."""
        hosts = _page_hosts(run)
        return {
            "success": True,
            "scraped": True,
            "format": "CSV",
            "bytes": self.paths.scraped.stat().st_size,
            "rows": run["rows_saved"],
            "dataset_title": f"Scraped by the agents' own code (scraper_v{run['version']}.py)",
            "dataset_organization": ", ".join(hosts) or "web",
            "dataset_url": f"https://{hosts[0]}" if hosts else "",
        }

    def call_search_open_data(self, query: str):
        """Tool: query opendata.swiss, handing back short resource ids in
        place of real URLs (see `resource_lookup`)."""
        result = search_open_data(query=query, on_progress=self.on_progress)
        self.results["opendata"] = result

        # Short, stable resource ids instead of making the model retype a
        # long URL to pick one (see opendata.py's search_open_data docs).
        agent_view_datasets = []
        for dataset in result["datasets"]:
            resource_views = []
            for res in dataset["resources"]:
                rid = f"r{len(self.resource_lookup)}"
                self.resource_lookup[rid] = {
                    "url": res["url"],
                    "format": res["format"],
                    "dataset_title": dataset["title"],
                    "dataset_organization": dataset["organization"],
                    "dataset_url": dataset["url"],
                }
                resource_views.append({"id": rid, "format": res["format"]})
            agent_view_datasets.append(
                {
                    "title": dataset["title"],
                    "organization": dataset["organization"],
                    "url": dataset["url"],
                    "resources": resource_views,
                }
            )
        response = {
            "query": result["query"],
            "total_found": result["total_found"],
            "datasets": agent_view_datasets,
        }
        if result.get("error"):
            response["error"] = result["error"]
        return response

    def call_download_dataset(self, resource_id: str):
        """Tool: really download the resource behind a short id from
        `call_search_open_data`, then auto-reject it if it doesn't look
        like individual-apartment listings."""
        picked = self.resource_lookup.get(resource_id)
        if not picked:
            return {
                "success": False,
                "error": (
                    f"Unknown resource_id '{resource_id}'. Use an id exactly as "
                    "returned by search_open_data."
                ),
            }

        result = download_dataset(
            resource_url=picked["url"],
            resource_format=picked["format"],
            out_path=str(self.paths.download),
            on_progress=self.on_progress,
        )
        if result.get("success"):
            self._accept_or_reject_download(result, picked)
        else:
            # A failed attempt must never leave an earlier, unrelated
            # download's preview looking like it belongs to this result.
            self.results["download_preview"] = {}

        result["resource_url"] = picked["url"]
        result["dataset_title"] = picked["dataset_title"]
        result["dataset_organization"] = picked["dataset_organization"]
        result["dataset_url"] = picked["dataset_url"]
        self.results["download"] = result
        return result

    def _accept_or_reject_download(self, result: dict, picked: dict) -> None:
        """Mutates `result` in place: accept the download (update
        `current_file`/`dataset_ready`) or reject and delete it."""
        looks_ok, reason = looks_like_listing_data(
            str(self.paths.download), picked["format"]
        )
        if not looks_ok:
            # Automatically recognized as unsuitable (aggregated / not
            # listing-level) — really delete it and report the rejection
            # as the actual tool result, regardless of what the agent
            # itself would have concluded.
            self.paths.download.unlink(missing_ok=True)
            result["success"] = False
            result["error"] = (
                f"Downloaded, but automatically rejected: {reason}. Try a different dataset."
            )
            self.current_file["path"] = ""
            self.current_file["format"] = ""
            self.dataset_ready = False
            self.results["download_preview"] = {}
        else:
            self.current_file["path"] = str(self.paths.download)
            self.current_file["format"] = picked["format"]
            self.dataset_ready = True
            self.capture_preview(
                "download_preview", self.current_file["path"], self.current_file["format"]
            )

    def call_discard_dataset(self, reason: str = ""):
        """Tool: really delete the current file because it turned out to
        be unsuitable, and clear it as the "current" dataset."""
        result = discard_dataset(
            path=self.current_file["path"], reason=reason, on_progress=self.on_progress
        )
        self.results["download"] = {}
        self.results["download_preview"] = {}
        self.current_file["path"] = ""
        self.current_file["format"] = ""
        self.dataset_ready = False
        return result

    # --- Data Engineer tools: Preparing & storing data -----------------

    def no_file_error(self) -> dict | None:
        """The error to report if there's no dataset file to work on."""
        # A tool the model calls before any successful download (or after
        # a discard_dataset) has nothing real to read — report that
        # plainly instead of letting a raw FileNotFoundError crash the
        # whole run.
        if not self.current_file["path"] or not Path(self.current_file["path"]).exists():
            return {
                "success": False,
                "error": "No dataset file is available yet — download (and confirm) one first.",
            }
        return None

    def call_preview_data(self, n: int = 10):
        """Tool: really read the first n rows of the current file."""
        err = self.no_file_error()
        if err:
            return err
        result = preview_data(
            path=self.current_file["path"],
            data_format=self.current_file["format"],
            n=n,
            on_progress=self.on_progress,
        )
        if result.get("error"):
            return result
        return {"columns": result["columns"], "n_rows_shown": len(result["rows"])}

    def call_profile_data(self):
        """Tool: really compute structure/quality stats for the current
        file (row/column counts, duplicates, missing values, dtypes)."""
        err = self.no_file_error()
        if err:
            return err
        result = profile_data(
            path=self.current_file["path"],
            data_format=self.current_file["format"],
            on_progress=self.on_progress,
        )
        # Not stored in results: the Step 4 card shows the profile of the
        # collected data (see app/demo_run.py's _brief_on_dataset), and a
        # later profile of the cleaned file mustn't overwrite it.
        return result

    def call_store_to_database(self, table_name: str):
        """Tool: really write the current (cleaned) file into a real
        local SQLite database table."""
        err = self.no_file_error()
        if err:
            return err
        result = store_to_database(
            source_path=self.current_file["path"],
            db_path=str(self.paths.db),
            table_name=table_name,
            on_progress=self.on_progress,
        )
        if result.get("error"):
            return result
        self.results["store"] = result
        return result

    def call_run_sql_query(self, query: str):
        """Tool: really run a read-only SELECT against the stored
        database to verify the storage worked."""
        result = run_sql_query(
            query=query, db_path=str(self.paths.db), on_progress=self.on_progress
        )
        if result.get("success", True) is not False:
            self.results["sql"] = result
        return result

    # --- Data Engineer (cleaning) / Data Analyst (enrichment) ----------

    def start_prep_stage(self, stage: str):
        """Begin a preparation stage: its scripts read whatever is the
        current dataset right now (the collected data for cleaning, the
        cleaned data for enrichment)."""
        self.prep.stage = stage
        self.prep.input = dict(self.current_file)
        if stage == "clean":
            self.prep.collected = dict(self.current_file)

    def end_prep_stage(self):
        """No preparation stage is active any more (storing comes next)."""
        self.prep.stage = ""

    def call_write_prep_code(self, code: str):
        """Tool: save (and statically check) a new version of this stage's
        own preparation script. Every version is kept for the UI/transcript."""
        stage = self.prep.stage
        if not stage:
            return {"success": False, "error": "No preparation stage is active right now."}
        version = len(self.prep.versions[stage]) + 1
        result = save_prep_code(code, self.paths.prep_dir, stage, version)
        self.prep.versions[stage].append({**result, "code": code})
        self.on_artifact("prep_code", {**result, "code": code})
        self.on_progress(
            f"{stage}_v{version}.py written ({result['lines']} lines) — "
            + ("check passed." if result["check_passed"] else "check FAILED.")
        )
        return result

    def _prep_run_refusal(self, version: int | None) -> dict | None:
        """Why run_prep_code can't run `version` right now, if it can't."""
        stage = self.prep.stage
        versions = self.prep.versions.get(stage, [])
        chosen = version or len(versions)
        if not stage:
            error = "No preparation stage is active right now."
        elif not versions:
            error = "No script written yet — call write_prep_code first."
        elif len(self.prep.runs[stage]) >= MAX_PREP_RUNS:
            error = f"Run budget for this stage used up ({MAX_PREP_RUNS} runs)."
        elif not self.prep.input["path"] or not Path(self.prep.input["path"]).exists():
            error = "There's no input dataset for this stage."
        elif not 1 <= chosen <= len(versions):
            error = f"Unknown version {version}."
        elif not versions[chosen - 1]["check_passed"]:
            return {
                "success": False,
                "error": "That version failed the code check — fix it first.",
                "problems": versions[chosen - 1]["problems"],
            }
        else:
            return None
        return {"success": False, "error": error}

    def call_run_prep_code(self, version: int | None = None):
        """Tool: really run one saved preparation script (see
        prep_code.py). If its output passes `judge_prep_output`, it becomes
        the current dataset."""
        refusal = self._prep_run_refusal(version)
        if refusal:
            return refusal
        stage = self.prep.stage
        version = version or len(self.prep.versions[stage])
        run_number = len(self.prep.runs[stage]) + 1
        work_dir = self.paths.prep_dir / f"{stage}_run_{run_number}_v{version}"
        result = run_prep_code(
            Path(self.prep.versions[stage][version - 1]["path"]),
            work_dir,
            Path(self.prep.input["path"]),
            self.prep.input["format"] or "CSV",
            work_dir / "output.csv",
            on_progress=self.on_progress,
        )
        result["stage"] = stage
        result["version"] = version
        result["accepted"], reason = judge_prep_output(stage, result)
        if result["accepted"]:
            earlier = [r for r in self.prep.runs[stage] if r["accepted"]]
            if earlier:
                result["lost_columns"] = [
                    c for c in earlier[-1]["columns"] if c not in result["columns"]
                    and c not in result["columns_before"]
                ]
            self._accept_prep_output(stage, result, work_dir / "output.csv")
        else:
            result["rejected_because"] = reason
        self.prep.runs[stage].append(result)
        self.on_artifact("prep_run", result)
        if result["accepted"]:
            # Right below the run card: how this step derived its columns.
            result["walkthrough_note"] = self.teaching.show_step_example(
                stage, f"{stage}_v{version}.py", self.prep.input, self.current_file["path"]
            )
        return _prep_agent_view(result)

    def _accept_prep_output(self, stage: str, result: dict, out_path: Path):
        """An accepted run's output becomes the current dataset."""
        target = self.paths.cleaned if stage == "clean" else self.paths.enriched
        shutil.copyfile(out_path, target)
        self.current_file["path"] = str(target)
        self.current_file["format"] = "CSV"
        self.results[stage] = {
            k: result[k]
            for k in (
                "version", "rows_before", "rows_after", "columns",
                "added_columns", "removed_columns", "missing_values",
            )
        }
        self.capture_preview("prepared_preview", str(target), "CSV")

    def prep_working_notes(self) -> str:
        """The coding agent's private memory of its latest run in the
        current preparation stage — same reason as scraper_working_notes."""
        stage = self.prep.stage
        if not stage:
            return ""
        if not self.prep.runs[stage]:
            # Live runs: without this, the enrichment coder spent a whole
            # phase answering questions about its plan and never wrote code.
            return (
                "Your private working notes (only you see these): nothing has run yet in "
                f"this {PREP_STAGE_LABELS[stage]} stage — no script exists, so no data has "
                "changed. Talking about the plan doesn't change the data: on your turn, "
                "call write_prep_code with your complete script and then run_prep_code."
            )
        run = self.prep.runs[stage][-1]
        code = self.prep.versions[stage][run["version"] - 1]["code"]
        verdict = (
            "ACCEPTED as the current dataset"
            if run["accepted"]
            else f"REJECTED: {run['rejected_because']}"
        )
        lines = [
            "Your private working notes (only you see these) from your LAST run in this "
            f"stage — {stage}_v{run['version']}.py "
            f"({len(self.prep.runs[stage])}/{MAX_PREP_RUNS} runs used):",
            f"- exit code {run['exit_code']}{' (timed out)' if run['timed_out'] else ''}, "
            f"{run['rows_before']} → {run['rows_after']} rows, {verdict}",
            f"- input columns: {', '.join(run['columns_before'])}",
        ]
        for diagnosis in (_failed_requests_diagnosis(run["requests"]),
                          _sparse_columns_diagnosis(run),
                          _lost_columns_diagnosis(run),
                          _quality_diagnosis(run)):
            if diagnosis:
                lines.append(f"- {diagnosis}")
        if run["saved"]:
            lines.append(f"- output dtypes: {json.dumps(run['dtypes'])}")
            lines.append(f"- output missing values: {json.dumps(run['missing_values'])}")
        if run["requests"]:
            lines.append(f"- {len(run['requests'])} web requests: " + "; ".join(
                f"{e['url']} -> {e.get('blocked') or e.get('status')}"
                for e in run["requests"][:5]
            ))
            structure = [{"url": e["url"], **e["shape"]} for e in run["requests"] if e.get("shape")]
            if structure:
                lines.append(
                    "- REAL response structure (use exactly these key names):\n"
                    + json.dumps(structure, ensure_ascii=False)
                )
        lines.append(f"- last printed output / traceback:\n{run['output_tail'][-800:]}")
        lines.append(f"- the code you ran:\n{code}")
        if self.teaching.is_stuck(stage):
            lines.append(STUCK_HINT.format(stage=stage))
        return "\n".join(lines)

    # --- All agents: optional sketch ------------------------------------

    def call_make_sketch(self, kind: str = "ascii", content: str = "", title: str = ""):
        """Tool: hand through a diagram the agent authored itself."""
        self.results["sketch"] = make_sketch(content=content, kind=kind, title=title)
        return {
            "ok": True
        }  # keep the tool-result text small; the UI reads results["sketch"] directly

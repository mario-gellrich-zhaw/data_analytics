"""Per-run wrappers around the pure tool functions in opendata.py,
preparation.py and scraper.py.

Those functions are stateless; a live run needs a bit of shared state on
top of them — which file is "current" right now, the real results captured
so each phase's result card can be built, and short ids handed to the
model in place of long opendata.swiss resource URLs. `RunTools` bundles
that state with the actual tool-calling wrappers the agents invoke, one
fresh instance per run.
"""

import json
import shutil
from pathlib import Path
from typing import Callable, NamedTuple

from tools.opendata import discard_dataset, download_dataset, search_open_data
from tools.preparation import (
    clean_data,
    make_sketch,
    preview_data,
    profile_data,
    run_sql_query,
    store_to_database,
)
from tools.scraper import run_scraper_code, save_scraper_code
from tools.validation import check_scraped_fields, filled_share, looks_like_listing_data

MAX_SCRAPER_RUNS = 6  # per demo run — each scraper run may make up to 15 requests

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


class DataPaths(NamedTuple):
    """The real files/folders one run's tools read from / write to."""

    download: Path
    scraped: Path  # the accepted output of the agents' own scraper
    scrapers_dir: Path  # every scraper version the agents wrote, plus each run's log
    cleaned: Path
    db: Path


class RunTools:
    """Real-tool state and call wrappers for one run.

    `results` holds the real, latest result of each tool a phase's result
    card needs to show (see app/demo_run.py's step3_result/step4_result); the
    other attributes track which file preview/profile/clean/store act on
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
        self.results: dict[str, dict] = {
            "opendata": {},
            "download": {},
            "download_preview": {},  # first 10 raw rows, captured right after download
            "profile": {},
            "clean": {},
            "clean_preview": {},  # first 10 cleaned rows, captured right after cleaning
            "store": {},
            "sql": {},
            "sketch": {},
        }
        self.resource_lookup: dict[str, dict] = {}  # short id -> real resource info
        self.current_file = {"path": str(paths.download), "format": "CSV"}
        self.dataset_ready = False  # True only while current_file is a real, undiscarded download

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
            "clean_data": self.call_clean_data,
            "store_to_database": self.call_store_to_database,
            "run_sql_query": self.call_run_sql_query,
            "make_sketch": self.call_make_sketch,
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
            return {"success": False, "error": "No scraper written yet — call write_scraper_code first."}
        if len(self.scraper_runs) >= MAX_SCRAPER_RUNS:
            return {
                "success": False,
                "error": f"Scraper run budget used up ({MAX_SCRAPER_RUNS} runs). Work with what you have or use open data.",
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
        agent_view.update({k: v for k, v in result.items() if k != "requests"})
        agent_view["requests"] = [
            {k: e.get(k) for k in ("url", "status", "blocked") if e.get(k) is not None}
            for e in result["requests"]
        ]
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
        lines = [
            "Your private working notes (only you see these) from your LAST scraper run — "
            f"scraper_v{run['version']}.py ({len(self.scraper_runs)}/{MAX_SCRAPER_RUNS} runs used):",
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
        return "\n".join(lines)

    def _scraped_download_result(self, run: dict) -> dict:
        """Shape an accepted scraper run like a download result, so the
        Step 3 card and Step 4's tools treat it the same way."""
        hosts = sorted(
            {
                e["url"].split("/")[2]
                for e in run["requests"]
                if e.get("kind") == "page" and e.get("status") == 200
            }
        )
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

    def _no_file_error(self) -> dict | None:
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
        err = self._no_file_error()
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
        err = self._no_file_error()
        if err:
            return err
        result = profile_data(
            path=self.current_file["path"],
            data_format=self.current_file["format"],
            on_progress=self.on_progress,
        )
        if result.get("error"):
            return result
        self.results["profile"] = result
        return result

    def call_clean_data(self, drop_duplicates: bool = True, drop_missing_in: list | None = None):
        """Tool: really drop duplicate/incomplete rows and write a cleaned
        file, which becomes the new "current" file."""
        err = self._no_file_error()
        if err:
            return err
        result = clean_data(
            source_path=self.current_file["path"],
            data_format=self.current_file["format"],
            out_path=str(self.paths.cleaned),
            drop_duplicates=drop_duplicates,
            drop_missing_in=drop_missing_in,
            on_progress=self.on_progress,
        )
        if result.get("error"):
            return result
        self.results["clean"] = result
        self.current_file["path"] = str(self.paths.cleaned)
        self.current_file["format"] = "CSV"
        self.capture_preview(
            "clean_preview", self.current_file["path"], self.current_file["format"]
        )
        return result

    def call_store_to_database(self, table_name: str):
        """Tool: really write the current (cleaned) file into a real
        local SQLite database table."""
        err = self._no_file_error()
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

    # --- All agents: optional sketch ------------------------------------

    def call_make_sketch(self, kind: str = "ascii", content: str = "", title: str = ""):
        """Tool: hand through a diagram the agent authored itself."""
        self.results["sketch"] = make_sketch(content=content, kind=kind, title=title)
        return {
            "ok": True
        }  # keep the tool-result text small; the UI reads results["sketch"] directly

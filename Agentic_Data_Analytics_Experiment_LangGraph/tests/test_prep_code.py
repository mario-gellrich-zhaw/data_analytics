"""Offline tests for the agent-written data-preparation tools: the static
code check, a real sandboxed run on a small CSV, the acceptance rules, and
RunTools' stage handling.

Run from the app folder:  python -m unittest discover tests
"""

# Test method names say what each test checks.
# pylint: disable=missing-function-docstring

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from tools import prep_code  # noqa: E402  pylint: disable=wrong-import-position
from tools import run_tools  # noqa: E402  pylint: disable=wrong-import-position

LISTINGS = pd.DataFrame(
    {
        "listing_id": [1, 2, 2, 3, 4],
        "rent_gross_chf": [2000, 1500, 1500, None, 3100],
        "rooms": ["3.5", "2", "2", "4", "4.5"],
        "living_space_m2": [80, 50, 50, 95, 110],
        "description": ["Loft mit Seesicht", "Nett", "Nett", "Ruhig", "Seesicht, Balkon"],
    }
)

CLEAN_SCRIPT = """
import pandas as pd
import prep_kit
df = prep_kit.load_data()
df = df.drop_duplicates(subset=["listing_id"]).dropna(subset=["rent_gross_chf"])
df["rooms"] = pd.to_numeric(df["rooms"])
print("rows left:", len(df))
prep_kit.save_data(df)
"""

ENRICH_SCRIPT = """
import prep_kit
df = prep_kit.load_data()
df["price_per_m2"] = (df["rent_gross_chf"] / df["living_space_m2"]).round(2)
df["luxurious"] = df["description"].str.upper().str.contains("LOFT|SEESICHT").astype(int)
prep_kit.save_data(df.drop(columns=["description"]))
"""


class CheckPrepCodeTest(unittest.TestCase):
    """The static whitelist check of agent-written preparation code."""

    def test_accepts_typical_scripts(self):
        self.assertEqual(prep_code.check_prep_code(CLEAN_SCRIPT), [])
        self.assertEqual(prep_code.check_prep_code(ENRICH_SCRIPT), [])
        lookup = (
            "import prep_kit, scraper_kit\ndf = prep_kit.load_data()\n"
            "try:\n    p = scraper_kit.polite_get('https://api3.geo.admin.ch/x', params={'a': 1})\n"
            "except scraper_kit.ScrapeBlocked as e:\n    print(e)\nprep_kit.save_data(df)\n"
        )
        self.assertEqual(prep_code.check_prep_code(lookup), [])

    def test_rejects_file_and_web_access_of_its_own(self):
        for code in (
            "import pandas as pd\ndf = pd.read_csv('x.csv')",
            "from pandas import read_csv",
            "import prep_kit\nprep_kit.load_data().to_csv('x.csv')",
            "import numpy as np\nnp.load('x.npy')",
            "import pandas as pd\npd.io.common",
            "import prep_kit\nprep_kit.load_data().query('a > 1')",
            "import os",
            "import requests",
            "import prep_kit\nprep_kit.IN_PATH",
            "import scraper_kit\nscraper_kit.save_rows([])",
            "import prep_kit\nprep_kit.load_data()",  # never saves its result
        ):
            with self.subTest(code=code):
                self.assertNotEqual(prep_code.check_prep_code(code), [])


def _run(tmp: str, code: str, in_df: pd.DataFrame) -> dict:
    tmp_path = Path(tmp)
    in_path = tmp_path / "in.csv"
    in_df.to_csv(in_path, index=False)
    script = tmp_path / "s.py"
    script.write_text(code, encoding="utf-8")
    work_dir = tmp_path / "w"
    return prep_code.run_prep_code(script, work_dir, in_path, "CSV", work_dir / "out.csv")


class RunPrepCodeTest(unittest.TestCase):
    """Really running a saved preparation script on a small CSV."""

    def test_cleaning_run_reports_real_before_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(tmp, CLEAN_SCRIPT, LISTINGS)
        self.assertEqual(result["exit_code"], 0, result["output_tail"])
        self.assertEqual((result["rows_before"], result["rows_after"]), (5, 3))
        self.assertIn("rows left: 3", result["output_tail"])
        self.assertEqual(result["dtypes"]["rooms"], "float64")
        self.assertEqual(prep_code.judge_prep_output("clean", result), (True, ""))

    def test_enrichment_run_lists_new_and_removed_columns(self):
        cleaned = LISTINGS.drop_duplicates(subset=["listing_id"]).dropna()
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(tmp, ENRICH_SCRIPT, cleaned)
        self.assertEqual(result["added_columns"], ["price_per_m2", "luxurious"])
        self.assertEqual(result["removed_columns"], ["description"])
        self.assertEqual(prep_code.judge_prep_output("enrich", result), (True, ""))

    def test_filling_in_missing_rents_is_rejected(self):
        imputing = (
            "import prep_kit\ndf = prep_kit.load_data()\n"
            "df['rent_gross_chf'] = df['rent_gross_chf'].fillna(df['rent_gross_chf'].median())\n"
            "prep_kit.save_data(df.drop_duplicates(subset=['listing_id']))\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(tmp, imputing, LISTINGS)
        self.assertEqual(result["filled_target_values"], {"rent_gross_chf": 1})
        ok, reason = prep_code.judge_prep_output("clean", result)
        self.assertFalse(ok)
        self.assertIn("training label", reason)

    def test_missing_values_turned_into_text_are_rejected(self):
        title_casing = (
            "import prep_kit\ndf = prep_kit.load_data().drop_duplicates(subset=['listing_id'])\n"
            "df['street'] = df['street'].astype(str).str.title()\n"
            "prep_kit.save_data(df)\n"
        )
        with_street = LISTINGS.assign(street=["a", None, None, "b", None])
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(tmp, title_casing, with_street)
        self.assertEqual(result["stringified_missing"], {"street": 2})
        ok, reason = prep_code.judge_prep_output("clean", result)
        self.assertFalse(ok)
        self.assertIn("'nan'", reason)

    def test_implausible_values_are_reported(self):
        odd = LISTINGS.assign(living_space_m2=[80, 523, 523, 95, 110])
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(tmp, CLEAN_SCRIPT, odd)
        self.assertTrue(any("m² per room" in p for p in result["quality_issues"]))

    def test_crash_shows_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            crashing = "import prep_kit\nprep_kit.load_data()['nope']\nprep_kit.save_data(None)"
            result = _run(tmp, crashing, LISTINGS)
        self.assertNotEqual(result["exit_code"], 0)
        self.assertIn("KeyError", result["output_tail"])
        ok, reason = prep_code.judge_prep_output("clean", result)
        self.assertFalse(ok)
        self.assertIn("crashed", reason)


class JudgePrepOutputTest(unittest.TestCase):
    """The rules every preparation run's output is judged by."""

    BASE = {
        "timed_out": False,
        "exit_code": 0,
        "saved": True,
        "rows_before": 100,
        "rows_after": 100,
        "columns_before": ["listing_id", "rent"],
        "columns": ["listing_id", "rent", "new"],
        "added_columns": ["new"],
        "listing_id_unique": True,
    }

    def judge(self, stage, **changes):
        return prep_code.judge_prep_output(stage, {**self.BASE, **changes})

    def test_cleaning_that_drops_most_rows_is_rejected(self):
        ok, reason = self.judge("clean", rows_after=30)
        self.assertFalse(ok)
        self.assertIn("70%", reason)

    def test_enrichment_must_keep_every_row_and_add_a_column(self):
        self.assertFalse(self.judge("enrich", rows_after=130)[0])
        self.assertFalse(self.judge("enrich", added_columns=[])[0])

    def test_enrichment_column_with_one_value_everywhere_is_rejected(self):
        ok, reason = self.judge("enrich", added_column_values={"new": 1})
        self.assertFalse(ok)
        self.assertIn("same value", reason)
        self.assertTrue(self.judge("enrich", added_column_values={"new": 2})[0])
        # too few listings to tell a bug from a fact
        self.assertTrue(
            self.judge("enrich", rows_before=5, rows_after=5, added_column_values={"new": 1})[0]
        )

    def test_cleaning_listings_with_coordinates_must_keep_only_zurich(self):
        with_coords = {"columns_before": ["listing_id", "lat", "lon"]}
        ok, reason = self.judge("clean", **with_coords)
        self.assertFalse(ok)
        self.assertIn("no 'canton' column", reason)
        ok, reason = self.judge("clean", **with_coords, canton_counts={"ZH": 90, "SG": 3})
        self.assertFalse(ok)
        self.assertIn("SG: 3", reason)
        self.assertTrue(self.judge("clean", **with_coords, canton_counts={"ZH": 93})[0])
        # no coordinates (e.g. an open-data table): nothing to look up
        self.assertTrue(self.judge("clean")[0])

    def test_listing_id_must_stay_unique(self):
        self.assertFalse(self.judge("clean", listing_id_unique=False)[0])
        self.assertFalse(self.judge("enrich", columns=["rent", "new"])[0])


class RunToolsPrepStageTest(unittest.TestCase):
    """RunTools' stage handling: cleaning feeds enrichment."""

    def test_accepted_stages_chain_clean_into_enrich(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            paths = run_tools.DataPaths(*(tmp_path / name for name in "abcdefgh"))
            tools = run_tools.RunTools(
                paths, on_progress=lambda _: None, on_artifact=lambda *_: None
            )
            raw = tmp_path / "raw.csv"
            LISTINGS.to_csv(raw, index=False)
            tools.current_file = {"path": str(raw), "format": "CSV"}

            self.assertIn("error", tools.call_run_prep_code())  # no stage active yet
            tools.start_prep_stage("clean")
            tools.call_write_prep_code(CLEAN_SCRIPT)
            cleaned = tools.call_run_prep_code()
            self.assertTrue(cleaned["accepted"], cleaned.get("rejected_because"))
            self.assertEqual(tools.current_file["path"], str(paths.cleaned))
            self.assertIn("clean_v1.py", tools.prep_working_notes())

            tools.start_prep_stage("enrich")
            self.assertIn("nothing has run yet", tools.prep_working_notes())  # fresh per stage
            tools.call_write_prep_code(ENRICH_SCRIPT)
            enriched = tools.call_run_prep_code()
            self.assertTrue(enriched["accepted"], enriched.get("rejected_because"))
            added = tools.results["enrich"]["added_columns"]
            self.assertEqual(added, ["price_per_m2", "luxurious"])
            self.assertEqual(len(pd.read_csv(paths.enriched)), 3)
            self.assertIn("Real run of enrich_v1.py", enriched["team_note"])
            self.assertIn("price_per_m2 (100% filled)", enriched["team_note"])
            self.assertIn("ACCEPTED", enriched["team_note"])


class SparseColumnsTest(unittest.TestCase):
    """A lookup tried on a few rows only must not read as finished."""

    def test_mostly_empty_new_column_gets_a_diagnosis(self):
        result = {
            "rows_after": 65,
            "added_columns": ["municipality", "price_per_m2"],
            "missing_values": {"municipality": 60, "price_per_m2": 5},
        }
        diagnosis = run_tools._sparse_columns_diagnosis(result)  # pylint: disable=protected-access
        self.assertIn("municipality (8% filled)", diagnosis)
        self.assertNotIn("price_per_m2", diagnosis)


class StatusTagTest(unittest.TestCase):
    """A reply without a status tag keeps the agent's earlier vote."""

    def test_missing_tag_keeps_previous_readiness(self):
        from agents.graph import record_turn  # pylint: disable=import-outside-toplevel

        state = {
            "pending_speaker": "Data Engineer",
            "final_text": "Looks complete to me.",
            "ready": {"Data Engineer": True},
            "transcript": [],
            "turns_in_phase": 3,
            "turn_idx": 3,
            "used_tool": False,
            "team_notes": [],
            "recent_turns": [],
        }
        self.assertTrue(record_turn(state)["ready"]["Data Engineer"])
        state["final_text"] = "One more fix needed.\n[STATUS: CONTINUE]"
        self.assertFalse(record_turn(state)["ready"]["Data Engineer"])


class StatusTagParsingTest(unittest.TestCase):
    """The ways models really end a reply with their status."""

    def status(self, text):
        from agents.graph import STATUS_TAG_RE  # pylint: disable=import-outside-toplevel

        match = STATUS_TAG_RE.search(text)
        tag = (match.group(1) or match.group(2)).upper() if match else None
        return tag, STATUS_TAG_RE.sub("", text).strip()

    def test_tag_forms_are_recognised_and_stripped(self):
        self.assertEqual(self.status("Done.\n[STATUS: NEXT]"), ("NEXT", "Done."))
        self.assertEqual(self.status("One more fix. [status: continue]"),
                         ("CONTINUE", "One more fix."))
        self.assertEqual(self.status("All good.\nNEXT"), ("NEXT", "All good."))
        # a live run's Product Manager wrote it straight after the sentence
        self.assertEqual(self.status("Let's proceed to implement them. NEXT"),
                         ("NEXT", "Let's proceed to implement them."))
        self.assertEqual(self.status("Ship it. **NEXT**"), ("NEXT", "Ship it."))

    def test_ordinary_words_are_not_a_tag(self):
        self.assertEqual(self.status("Let's move on to the next."),
                         (None, "Let's move on to the next."))
        self.assertEqual(self.status("on to the NEXT step"), (None, "on to the NEXT step"))


class DataQualityIssuesTest(unittest.TestCase):
    """What the briefing and every cleaning run point out, from the data."""

    def issues(self, **columns):
        from tools.validation import data_quality_issues  # pylint: disable=import-outside-toplevel

        base = {"listing_id": [1, 2, 3, 4]}
        return data_quality_issues(pd.DataFrame({**base, **columns}))

    def test_impossible_building_years(self):
        found = self.issues(year_built=[0, 1990, 2010, None],
                            year_renovated=[None, 1980, 2020, None])
        self.assertTrue(any(i.startswith("year_built: 1 value") for i in found))
        self.assertTrue(any("year_renovated before year_built" in i for i in found))

    def test_same_flat_under_several_ids(self):
        found = self.issues(street=["Weg 1", "weg 1 ", "Gasse 2", None],
                            rooms=[3.5, 3.5, 2.0, 3.5], rent_gross_chf=[2000, 2000, 1500, 2000])
        self.assertTrue(any("likely duplicates: 1 flat" in i and "1, 2" in i for i in found))

    def test_one_place_spelled_two_ways(self):
        found = self.issues(city=["Zürich", "Zurich", "Uster", "Uster"])
        self.assertEqual(found, ["city: the same name spelled differently — 'Zurich' / 'Zürich'"])


class CirclingGuardTest(unittest.TestCase):
    """A phase ends once most agents are done and nobody does anything real."""

    def state(self, ready, turns):
        return {"phase_agents": [object()] * 3, "ready": ready, "recent_turns": turns}

    def test_majority_done_and_two_talk_only_rounds_end_the_phase(self):
        from agents.graph import _circling  # pylint: disable=import-outside-toplevel

        talk = [{"empty": False, "used_tool": False}] * 6
        ready = {"Product Manager": False, "Data Analyst": True, "Data Engineer": True}
        self.assertTrue(_circling(self.state(ready, talk)))

    def test_minority_done_or_tool_use_keeps_it_going(self):
        from agents.graph import _circling  # pylint: disable=import-outside-toplevel

        talk = [{"empty": False, "used_tool": False}] * 6
        one_ready = {"Product Manager": True, "Data Analyst": False, "Data Engineer": False}
        self.assertFalse(_circling(self.state(one_ready, talk)))
        two_ready = {"Product Manager": True, "Data Analyst": True, "Data Engineer": False}
        with_tool = talk[:5] + [{"empty": False, "used_tool": True}]
        self.assertFalse(_circling(self.state(two_ready, with_tool)))


class StallGuardTest(unittest.TestCase):
    """The graph ends a phase that has run dry."""

    def state(self, turns):
        return {"phase_agents": [object()] * 3, "recent_turns": turns}

    def test_two_quiet_rounds_end_the_phase(self):
        from agents.graph import _stalled  # pylint: disable=import-outside-toplevel

        quiet = [{"empty": e, "used_tool": False} for e in (True, True, False) * 2]
        self.assertTrue(_stalled(self.state(quiet)))

    def test_talking_or_tool_use_keeps_it_going(self):
        from agents.graph import _stalled  # pylint: disable=import-outside-toplevel

        talking = [{"empty": False, "used_tool": False}] * 6
        self.assertFalse(_stalled(self.state(talking)))
        with_tool = [{"empty": True, "used_tool": False}] * 5
        with_tool.append({"empty": False, "used_tool": True})
        self.assertFalse(_stalled(self.state(with_tool)))


if __name__ == "__main__":
    unittest.main()

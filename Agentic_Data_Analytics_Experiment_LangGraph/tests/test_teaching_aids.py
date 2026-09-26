"""Offline tests for the teaching aids: the real examples agents show the
class (show_to_class) and the look at earlier runs when stuck.

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

from tools import history, run_tools, teaching  # noqa: E402  pylint: disable=wrong-import-position

COLLECTED = pd.DataFrame(
    {
        "listing_id": [11, 12],
        "rent_gross_chf": [2100, 1800],
        "description": ["Helle Wohnung mit Balkon und Seesicht", "Ruhige Lage"],
    }
)

PAST_RUN = """## Step 4/4 · Preparing & storing data — enrichment
**enrich_v1.py — enrichment code written by the Data Analyst (3 lines)**, check passed:
```python
broken = True
```

**Ran `enrich_v1.py`:** crashed (exit code 1), 20 → 0 rows, 0 web request(s), 0.5 s.
- Not accepted: the script crashed (see the traceback in output_tail)
Printed output:
```
Traceback (most recent call last):
KeyError: 'gemeindename'
```

**enrich_v2.py — enrichment code written by the Data Analyst (2 lines)**, check passed:
```python
name = attrs['gemname']
```

**Ran `enrich_v2.py`:** exit code 0, 20 → 20 rows, 21 web request(s), 7.5 s.
- Accepted as the current dataset.
"""


def _tools(tmp: Path) -> run_tools.RunTools:
    names = ["dl.csv", "scraped.csv", "scrapers", "cleaned.csv", "db.db", "prep",
             "enriched.csv", "history"]
    paths = run_tools.DataPaths(*(tmp / name for name in names))
    return run_tools.RunTools(paths, on_progress=lambda _: None, on_artifact=lambda *_: None)


class ShowToClassTest(unittest.TestCase):
    """Exhibits come from real files and are capped per phase."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        tmp = Path(self.tmp.name)
        self.tools = _tools(tmp)
        COLLECTED.to_csv(tmp / "raw.csv", index=False)
        self.tools.current_file = {"path": str(tmp / "raw.csv"), "format": "CSV"}
        self.tools.start_prep_stage("clean")
        enriched = COLLECTED.assign(has_balcony=[1, 0], rent_gross_chf=[2100.0, 1800.0])
        enriched.to_csv(tmp / "now.csv", index=False)
        self.tools.current_file = {"path": str(tmp / "now.csv"), "format": "CSV"}

    def tearDown(self):
        self.tmp.cleanup()

    def test_single_case_shows_text_next_to_derived_value(self):
        shown = []
        self.tools.on_artifact = lambda kind, data: shown.append((kind, data))
        result = self.tools.teaching.call_show_to_class(
            "single_case", "Balkon in the text becomes has_balcony = 1",
            listing_id="11", columns=["description", "has_balcony"],
        )
        self.assertTrue(result["shown"])
        self.assertIn("listing 11", result["team_note"])
        fields = {f["column"]: f for f in shown[0][1]["fields"]}
        self.assertIn("Balkon", fields["description"]["after"])
        balcony = fields["has_balcony"]
        self.assertEqual((balcony["after"], balcony["status"]), (1, "new"))

    def test_code_excerpt_comes_from_a_script_written_this_run(self):
        self.tools.call_write_prep_code("import prep_kit\ndf = prep_kit.load_data()\n"
                                        "prep_kit.save_data(df)\n")
        shown = []
        self.tools.on_artifact = lambda kind, data: shown.append(data)
        self.tools.teaching.call_show_to_class("code", "the save", script="clean_v1.py",
                                      start_line=3, end_line=3)
        self.assertEqual(shown[0]["lines"], ["prep_kit.save_data(df)"])
        missing = self.tools.teaching.call_show_to_class("code", "x", script="nope.py")
        self.assertFalse(missing["shown"])

    def test_caption_is_required(self):
        refused = self.tools.teaching.call_show_to_class("rows", "  ", n=2)
        self.assertFalse(refused["shown"])
        self.assertIn("caption", refused["error"])

    def test_unknown_listing_and_cap_per_phase(self):
        self.assertIn("no listing", self.tools.teaching.call_show_to_class(
            "single_case", "x", listing_id="999")["error"])
        for _ in range(teaching.MAX_EXHIBITS_PER_PHASE):
            self.assertTrue(self.tools.teaching.call_show_to_class("rows", "x", n=2)["shown"])
        self.assertFalse(self.tools.teaching.call_show_to_class("rows", "x")["shown"])
        self.tools.teaching.new_phase()
        self.assertTrue(self.tools.teaching.call_show_to_class("rows", "x")["shown"])


class LookUpPastRunsTest(unittest.TestCase):
    """Earlier runs only open up once the agent is stuck."""

    def test_parses_accepted_script_and_problems(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "conversation_20260101_120000.md").write_text(PAST_RUN, encoding="utf-8")
            result = history.look_up_past_runs(Path(tmp), "enrich")
        self.assertEqual(result["accepted_scripts"][0]["file"], "enrich_v2.py")
        self.assertIn("gemname", result["accepted_scripts"][0]["code"])
        self.assertIn("KeyError: 'gemeindename'", result["past_problems"][0])

    def test_refused_until_stuck(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = _tools(Path(tmp))
            (Path(tmp) / "history").mkdir()
            tools.start_prep_stage("enrich")
            self.assertIn("Not available yet", tools.teaching.call_look_up_past_runs()["error"])
            tools.prep.runs["enrich"] = [{"accepted": False}, {"accepted": False}]
            self.assertTrue(tools.teaching.is_stuck("enrich"))
            result = tools.teaching.call_look_up_past_runs()
            self.assertEqual(result["runs_searched"], 0)
            self.assertIn("Stuck at the enrich step", result["team_note"])

    def test_one_success_means_not_stuck(self):
        with tempfile.TemporaryDirectory() as tmp:
            tools = _tools(Path(tmp))
            tools.prep.runs["clean"] = [{"accepted": False}, {"accepted": True}]
            self.assertFalse(tools.teaching.is_stuck("clean"))
            tools.scraper_runs = [{"rows_saved": 0}, {"rows_saved": 0}]
            self.assertTrue(tools.teaching.is_stuck("scraper"))


if __name__ == "__main__":
    unittest.main()

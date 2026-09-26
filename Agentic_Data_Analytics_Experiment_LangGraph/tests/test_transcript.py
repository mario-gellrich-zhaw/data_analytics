"""Offline tests for the saved transcript (Markdown + HTML): which tools a
turn used, script versions that never ran, and the saved page's header.

Run from the app folder:  python -m unittest discover tests
"""

# Test method names say what each test checks.
# pylint: disable=missing-function-docstring

import sys
import unittest
from datetime import datetime
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from reporting import transcript  # noqa: E402  pylint: disable=wrong-import-position

HISTORY = [
    {"kind": "phase", "step": 3, "step_label": "Collecting data", "sub_label": "action",
     "goal": "Our goal is to collect data."},
    {"kind": "scraper_code", "data": {"version": 1, "lines": 1, "check_passed": True,
                                      "code": "import scraper_kit"}},
    {"kind": "scraper_code", "data": {"version": 2, "lines": 1, "check_passed": True,
                                      "code": "import scraper_kit"}},
    {"kind": "scraper_run", "data": {"version": 2, "exit_code": 0, "timed_out": False,
                                     "rows_saved": 120, "elapsed_seconds": 3.0,
                                     "requests": [], "output_tail": ""}},
    {"kind": "turn", "speaker": "Data Analyst", "text": "Scraped 120 listings.",
     "action": True, "tools": ["run_scraper", "preview_data", "run_scraper"]},
    {"kind": "turn", "speaker": "Data Engineer", "text": "Looks stable.", "action": True},
]
ASSETS = transcript.PageAssets(
    css="",
    index_html=(
        '<header class="hero"><span class="eyebrow">Live demo</span><h1>Live title</h1>'
        '<p class="tagline">Watch them work live.</p><div class="legend">legend</div>'
        '<button id="start-btn">Start</button></header>'
    ),
    svg_markup="<svg></svg>",
)
START, END = datetime(2026, 9, 26, 12, 0), datetime(2026, 9, 26, 12, 5)
OUTCOME = {"status": "completed"}


class MarkdownTest(unittest.TestCase):
    def setUp(self):
        self.md = transcript.render_markdown(HISTORY, START, END, OUTCOME)

    def test_turn_names_the_tools_it_really_used(self):
        self.assertIn("Scraped 120 listings. _(🔧 run_scraper · preview_data)_", self.md)
        # an older saved turn without tool names
        self.assertIn("Looks stable. _(🔧 real action)_", self.md)

    def test_a_version_that_never_ran_says_so(self):
        v1, v2 = self.md.split("scraper_v2.py`**", 1)
        self.assertIn(transcript.NEVER_RUN_NOTE, v1)
        self.assertNotIn(transcript.NEVER_RUN_NOTE, v2)


class HtmlTest(unittest.TestCase):
    def setUp(self):
        self.page = transcript.render_html(HISTORY, START, END, OUTCOME, ASSETS)

    def test_saved_page_has_one_title_and_keeps_the_legend(self):
        self.assertEqual(self.page.count("<h1>"), 1)
        self.assertNotIn("Live title", self.page)
        self.assertNotIn("Watch them work live", self.page)
        self.assertNotIn("start-btn", self.page)
        self.assertIn("legend", self.page)

    def test_scripts_are_collapsed_until_clicked(self):
        self.assertIn('<details class="phase-card code-card"><summary><h2>🧑‍💻 scraper_v1.py',
                      self.page)
        self.assertNotIn("<details open", self.page)

    def test_data_tables_are_plain_scrollable_grids(self):
        from reporting.html_parts import data_table_html  # pylint: disable=import-outside-toplevel

        columns = [f"c{i}" for i in range(20)]
        rows = [[None if i % 3 else f"v{r}" for i in range(20)] for r in range(15)]
        table = data_table_html(columns, rows)
        self.assertIn('class="table-scroll data-scroll"', table)
        self.assertNotIn("turned", table)
        self.assertEqual(table.count("<tr>"), 11)  # header + the first 10 records
        self.assertIn('<td class="missing">—</td>', table)

    def test_bubble_names_its_tools(self):
        self.assertIn('<span class="tool-tag">🔧 run_scraper · preview_data</span>', self.page)


if __name__ == "__main__":
    unittest.main()

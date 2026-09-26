"""Offline tests for the walkthrough the app shows after every accepted
cleaning/enrichment run: how each new or changed column was derived.

Run from the app folder:  python -m unittest discover tests
"""

# Test method names say what each test checks.
# pylint: disable=missing-function-docstring

import json
import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

from tools import run_tools, walkthrough  # noqa: E402  pylint: disable=wrong-import-position

CLEANED = pd.DataFrame(
    {
        "listing_id": [1, 2, 3, 4],
        "description": [
            "Helle Wohnung, grosser Balkon mit Seesicht.",
            "Zwei Balkone und ein Lift.",
            "Ruhige Lage, Parkett.",
            "Moderne Küche, Lift im Haus.",
        ],
        "attributes": ["view", "lift", "parquetflooring", "lift"],
        "lat": [47.37, 47.40, 47.50, 47.20],
        "lon": [8.54, 8.50, 8.70, 8.60],
        "rooms": [3.5, 2.0, 4.5, 3.0],
    }
)

# Written the way the agents write theirs (see a saved enrich_vN.py).
ENRICH_SCRIPT = """import prep_kit
import scraper_kit

df = prep_kit.load_data()
amenity_dict = {
    'balcony': (['balkon', 'balcony'], ['balcony', 'balconygarden']),
    'lift': (['lift', 'aufzug'], ['lift']),
}
for amen, (desc_keys, attr_keys) in amenity_dict.items():
    df[f'has_{amen}'] = df.apply(
        lambda x: any(k in str(x['description']).lower() for k in desc_keys)
        or any(k in str(x['attributes']) for k in attr_keys),
        axis=1)

def get_municipality(lat, lon):
    return 'Zürich'

muni = df.apply(lambda x: get_municipality(x['lat'], x['lon']), axis=1)
df['municipality'] = muni
prep_kit.save_data(df)
"""


def _enriched() -> pd.DataFrame:
    return CLEANED.assign(
        has_balcony=[True, True, False, False],
        has_lift=[False, True, False, True],
        municipality=["Zürich"] * 4,
    )


class EnrichmentWalkthroughTest(unittest.TestCase):
    """Flags: evidence words from the agents' code, confirmed by the data."""

    def setUp(self):
        self.result = walkthrough.step_walkthrough(
            "enrich", CLEANED, _enriched(), ENRICH_SCRIPT, "enrich_v1.py"
        )
        self.features = {f["column"]: f for f in self.result["features"]}

    def test_balcony_evidence_comes_from_its_own_keyword_line(self):
        balcony = self.features["has_balcony"]
        self.assertEqual(balcony["evidence"], ["balkon"])  # 'balcony' never occurs in the text
        self.assertEqual(balcony["summary"],
                         "True for 2 of 4 listings (2 of them say so in the description)")

    def test_code_excerpt_is_the_keyword_line_and_the_loop(self):
        code = [line for ex in self.features["has_balcony"]["code"] for line in ex["lines"]]
        self.assertTrue(any("'balcony': (['balkon'" in line for line in code))
        self.assertTrue(any("df[f'has_{amen}']" in line for line in code))
        self.assertLessEqual(len(code), 10)  # just those, with a line of context each

    def test_records_show_the_word_in_the_text_and_one_contrast(self):
        balcony = self.features["has_balcony"]
        self.assertEqual(balcony["row_flags"], [True, True, False])
        description = balcony["headers"].index("description")
        self.assertIn("Balkon", balcony["rows"][0][description])
        self.assertEqual(balcony["rows"][0][-1], True)

    def test_lookup_shows_its_inputs_and_source_line(self):
        muni = self.features["municipality"]
        self.assertEqual(muni["headers"], ["listing", "lat", "lon", "municipality"])
        code = [line for ex in muni["code"] for line in ex["lines"]]
        self.assertTrue(any("muni = df.apply" in line for line in code))

    def test_result_is_plain_json(self):
        # numpy int64 listing ids once crashed a live run at json.dumps
        json.dumps(self.result)

    def test_overview_puts_inputs_next_to_new_columns(self):
        columns = self.result["overview"]["columns"]
        self.assertEqual(columns[:4], ["listing", "attributes", "lat", "lon"])
        self.assertIn("has_balcony", columns)


class CleaningWalkthroughTest(unittest.TestCase):
    """Changed columns: before -> after for the rows that changed."""

    def test_changed_rows_before_and_after(self):
        cleaned = CLEANED.assign(rooms=[3.0, 2.0, 4.0, 3.0])
        script = "import prep_kit\ndf = prep_kit.load_data()\n" \
                 "df['rooms'] = df['rooms'].astype(int)\nprep_kit.save_data(df)\n"
        result = walkthrough.step_walkthrough("clean", CLEANED, cleaned, script, "clean_v1.py")
        rooms = result["features"][0]
        self.assertEqual(rooms["summary"], "Changed in 2 of 4 listings")
        self.assertEqual(rooms["rows"][0][1:], ["3.5", "3.0"])
        self.assertEqual(rooms["code"][0]["start_line"], 3)


    def test_nothing_changed_says_so(self):
        result = walkthrough.step_walkthrough(
            "clean", CLEANED, CLEANED.copy(), "import prep_kit\n", "clean_v1.py"
        )
        self.assertEqual(result["features"], [])
        self.assertIn("No value changed", result["note"])


class SnippetTest(unittest.TestCase):
    """Long text is cut around the word that matters."""

    def test_snippet_centres_on_the_word(self):
        text = "x" * 200 + " mit Balkon " + "y" * 200
        cut = walkthrough.snippet(text, ["balkon"])
        self.assertIn("Balkon", cut)
        self.assertTrue(cut.startswith("…") and cut.endswith("…"))
        self.assertIsNone(walkthrough.snippet(float("nan"), []))


class ShownAfterAcceptedRunTest(unittest.TestCase):
    """The app shows the walkthrough right below every accepted run."""

    def test_walkthrough_follows_the_run_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            names = ["dl.csv", "scraped.csv", "scrapers", "cleaned.csv", "db.db", "prep",
                     "enriched.csv", "history"]
            tools = run_tools.RunTools(
                run_tools.DataPaths(*(Path(tmp) / n for n in names)),
                on_progress=lambda _: None, on_artifact=lambda *_: None,
            )
            CLEANED.to_csv(Path(tmp) / "raw.csv", index=False)
            tools.current_file = {"path": str(Path(tmp) / "raw.csv"), "format": "CSV"}
            events = []
            tools.on_artifact = lambda kind, data: events.append((kind, data))
            tools.start_prep_stage("enrich")
            tools.call_write_prep_code(ENRICH_SCRIPT.replace("import scraper_kit\n", ""))
            result = tools.call_run_prep_code()
        self.assertEqual([kind for kind, _ in events], ["prep_code", "prep_run", "walkthrough"])
        self.assertIn("has_balcony (listing 1: 'balkon' → True)", result["team_note"])


    def test_losing_earlier_features_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            names = ["dl.csv", "scraped.csv", "scrapers", "cleaned.csv", "db.db", "prep",
                     "enriched.csv", "history"]
            tools = run_tools.RunTools(
                run_tools.DataPaths(*(Path(tmp) / n for n in names)),
                on_progress=lambda _: None, on_artifact=lambda *_: None,
            )
            CLEANED.to_csv(Path(tmp) / "raw.csv", index=False)
            tools.current_file = {"path": str(Path(tmp) / "raw.csv"), "format": "CSV"}
            tools.start_prep_stage("enrich")
            tools.call_write_prep_code(ENRICH_SCRIPT.replace("import scraper_kit\n", ""))
            tools.call_run_prep_code()
            # the next version only does the lookup — the amenity flags are gone
            tools.call_write_prep_code(
                "import prep_kit\ndf = prep_kit.load_data()\n"
                "df['municipality'] = 'Zürich'\nprep_kit.save_data(df)\n"
            )
            result = tools.call_run_prep_code()
        self.assertEqual(result["lost_columns"], ["has_balcony", "has_lift"])
        self.assertIn("no longer contains has_balcony, has_lift", result["team_note"])
        self.assertIn("LAST accepted script is the whole step", result["diagnosis"])


if __name__ == "__main__":
    unittest.main()

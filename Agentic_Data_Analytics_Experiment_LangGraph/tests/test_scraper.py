"""Offline tests for the agent-written-scraper tools: the static code
check, scraper_kit's politeness rules (with mocked HTTP), and the runner.

Run from the app folder:  python -m unittest discover tests
"""

import csv
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR))

import scraper_tool  # noqa: E402  pylint: disable=wrong-import-position
from sandbox import scraper_kit  # noqa: E402  pylint: disable=wrong-import-position


class FakeResponse:  # pylint: disable=too-few-public-methods
    def __init__(self, status=200, text="", headers=None, url=""):
        self.status_code = status
        self.text = text
        self.content = text.encode()
        self.headers = headers or {"Content-Type": "application/json"}
        self.url = url


def fake_web(pages: dict):
    """requests.get stand-in: url -> FakeResponse (robots.txt allows all
    unless given)."""

    def get(url, **_kwargs):
        if url in pages:
            response = pages[url]
        elif url.endswith("/robots.txt"):
            response = FakeResponse(200, "User-agent: *\nAllow: /\n", {"Content-Type": "text/plain"})
        else:
            response = FakeResponse(404, "not found")
        response.url = response.url or url
        return response

    return get


class CheckScraperCodeTest(unittest.TestCase):
    def test_accepts_typical_scraper(self):
        code = (
            "import scraper_kit\nfrom urllib.parse import urljoin\nfrom bs4 import BeautifulSoup\n"
            "try:\n    p = scraper_kit.polite_get('https://flatfox.ch/x')\n"
            "except scraper_kit.ScrapeBlocked as e:\n    print(e)\n"
            "scraper_kit.save_rows([{'source': 'flatfox'}])\n"
        )
        self.assertEqual(scraper_tool.check_scraper_code(code), [])

    def test_rejects_ways_around_scraper_kit(self):
        for code in (
            "import requests",
            "import os",
            "import subprocess",
            "from urllib import request",
            "import urllib.request",
            "import socket",
            "open('x')",
            "eval('1')",
            "__import__('os')",
            "x = ().__class__",
            "import scraper_kit\nscraper_kit.requests.get('x')",
            "from scraper_kit import requests",
            "import scraper_kit\nk = scraper_kit\nk.requests",
            "import scraper_kit as k\nk._rows",
            "def f(:\n",
        ):
            with self.subTest(code=code):
                self.assertNotEqual(scraper_tool.check_scraper_code(code), [])


class ScraperKitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()  # pylint: disable=consider-using-with
        env = {
            "SCRAPER_KIT_LOG": os.path.join(self.tmp.name, "log.jsonl"),
            "SCRAPER_KIT_OUT": os.path.join(self.tmp.name, "out.csv"),
            "SCRAPER_KIT_MAX_REQUESTS": "3",
            "SCRAPER_KIT_MAX_ROWS": "2",
        }
        with mock.patch.dict(os.environ, env):
            self.kit = importlib.reload(scraper_kit)  # fresh module state per test
        self.kit.MIN_DELAY_SECONDS = self.kit.MAX_DELAY_SECONDS = 0

    def tearDown(self):
        self.tmp.cleanup()

    def get(self, pages, url):
        with mock.patch.object(self.kit.requests, "get", side_effect=fake_web(pages)):
            return self.kit.polite_get(url)

    def test_ok_page(self):
        page = self.get({"https://flatfox.ch/a": FakeResponse(200, '{"n": 1}')}, "https://flatfox.ch/a")
        self.assertEqual(page.json(), {"n": 1})

    def test_first_page_structure_logged_once_per_host(self):
        body = '{"count": 2, "results": [{"pk": 1, "rent_gross": 2000}]}'
        pages = {f"https://flatfox.ch/{i}": FakeResponse(200, body) for i in range(2)}
        self.get(pages, "https://flatfox.ch/0")
        self.get(pages, "https://flatfox.ch/1")
        log = scraper_tool._read_request_log(Path(self.kit.LOG_PATH))  # pylint: disable=protected-access
        shapes = [e["shape"] for e in log if e.get("shape")]
        self.assertEqual(len(shapes), 1)
        self.assertEqual(shapes[0]["keys_of_first_item_in_results"], ["pk", "rent_gross"])

    def test_domain_not_allowed(self):
        with self.assertRaisesRegex(self.kit.ScrapeBlocked, "domain not allowed"):
            self.get({}, "https://example.com/")

    def test_403_blocks_host_for_rest_of_run(self):
        pages = {"https://www.immoscout24.ch/a": FakeResponse(403, "no")}
        with self.assertRaisesRegex(self.kit.ScrapeBlocked, "HTTP 403"):
            self.get(pages, "https://www.immoscout24.ch/a")
        with self.assertRaisesRegex(self.kit.ScrapeBlocked, "already blocked"):
            self.get(pages, "https://www.immoscout24.ch/b")

    def test_429(self):
        with self.assertRaisesRegex(self.kit.ScrapeBlocked, "rate-limited"):
            self.get({"https://flatfox.ch/a": FakeResponse(429)}, "https://flatfox.ch/a")

    def test_cloudflare_challenge(self):
        challenge = FakeResponse(
            403, "<html><head><title>Just a moment...</title>", {"Content-Type": "text/html"}
        )
        with self.assertRaisesRegex(self.kit.ScrapeBlocked, "Cloudflare"):
            self.get({"https://www.immoscout24.ch/a": challenge}, "https://www.immoscout24.ch/a")

    def test_robots_disallow_and_robots_403(self):
        disallow = FakeResponse(200, "User-agent: *\nDisallow: /\n", {"Content-Type": "text/plain"})
        with self.assertRaisesRegex(self.kit.ScrapeBlocked, "robots.txt disallows"):
            self.get({"https://homegate.ch/robots.txt": disallow}, "https://homegate.ch/x")
        with self.assertRaisesRegex(self.kit.ScrapeBlocked, "robots.txt disallows"):
            self.get({"https://flatfox.ch/robots.txt": FakeResponse(403)}, "https://flatfox.ch/x")

    def test_request_budget(self):
        pages = {f"https://flatfox.ch/{i}": FakeResponse(200, "{}") for i in range(5)}
        for i in range(3):
            self.get(pages, f"https://flatfox.ch/{i}")
        with self.assertRaisesRegex(self.kit.ScrapeBlocked, "budget"):
            self.get(pages, "https://flatfox.ch/3")

    def test_redirect_off_allowlist(self):
        pages = {"https://flatfox.ch/r": FakeResponse(200, "{}", url="https://evil.example/")}
        with self.assertRaisesRegex(self.kit.ScrapeBlocked, "redirected"):
            self.get(pages, "https://flatfox.ch/r")

    def test_save_rows_schema_dedup_and_cap(self):
        with self.assertRaisesRegex(ValueError, "unknown column"):
            self.kit.save_rows([{"price": 1}])
        saved = self.kit.save_rows(
            [
                {"source": "flatfox", "listing_id": 1, "rooms": 3.5},
                {"source": "flatfox", "listing_id": 1, "rooms": 3.5},
                {"source": "flatfox", "listing_id": 2},
                {"source": "flatfox", "listing_id": 3},
            ]
        )
        self.assertEqual(saved, 2)  # duplicate skipped, capped at MAX_ROWS=2
        with open(self.kit.OUT_PATH, encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([r["listing_id"] for r in rows], ["1", "2"])
        self.assertEqual(list(rows[0]), list(self.kit.FIELDS))


class RunnerTest(unittest.TestCase):
    def test_env_has_no_secrets(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret", "HOME": "/x"}):
            env = scraper_tool.scraper_env(Path("log"), Path("out"))
        self.assertNotIn("OPENAI_API_KEY", env)
        self.assertNotIn("HOME", env)

    def test_runs_script_and_reports_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "s.py"
            script.write_text(
                "import scraper_kit\nprint('hello')\n"
                "scraper_kit.save_rows([{'source': 't', 'listing_id': i} for i in range(3)])\n",
                encoding="utf-8",
            )
            result = scraper_tool.run_scraper_code(script, Path(tmp) / "w", Path(tmp) / "w" / "o.csv")
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["rows_saved"], 3)
        self.assertIn("hello", result["output_tail"])

    def test_crash_returns_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "s.py"
            script.write_text("x = {}\nx['missing']\n", encoding="utf-8")
            result = scraper_tool.run_scraper_code(script, Path(tmp) / "w", Path(tmp) / "w" / "o.csv")
        self.assertNotEqual(result["exit_code"], 0)
        self.assertIn("KeyError", result["output_tail"])

    def test_app_modules_not_importable(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "s.py"
            script.write_text("import data_tool\n", encoding="utf-8")
            result = scraper_tool.run_scraper_code(script, Path(tmp) / "w", Path(tmp) / "w" / "o.csv")
        self.assertIn("ModuleNotFoundError", result["output_tail"])


if __name__ == "__main__":
    unittest.main()

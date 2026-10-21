"""Where the responses are stored.

- SheetStore: a central Google Sheet, reached through the Apps Script web app in
  google_apps_script/Code.gs (one open link, no Google account needed)
- SQLiteStore: a local SQLite file, used while no sheet link is configured
"""

import json
import sqlite3
import urllib.request
from datetime import datetime, timezone

from questions import QUESTIONS

COLUMNS = ["id", "created_at"] + [q["key"] for q in QUESTIONS]
TEXT_COLUMNS = {"created_at"} | {q["key"] for q in QUESTIONS if q["type"] == "choice"}


class StorageError(Exception):
    """The responses could not be saved or loaded."""


def typed_row(row):
    """Row as {column: value} with text columns as str and all others as int."""
    typed = {}
    for col in COLUMNS:
        value = row.get(col)
        if col in TEXT_COLUMNS:
            typed[col] = "" if value is None else str(value)
        else:
            typed[col] = int(value) if value not in (None, "") else None
    return typed


class SheetStore:
    def __init__(self, url, timeout=30):
        self.url = url
        self.timeout = timeout

    def _request(self, data=None):
        # Apps Script answers with a redirect; urllib follows it (POST becomes GET, as Apps Script expects)
        request = urllib.request.Request(self.url, data=data, headers={"Content-Type": "text/plain;charset=utf-8"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except (OSError, ValueError) as exc:
            raise StorageError(f"The central sheet could not be reached ({exc}).") from exc

    def add(self, answers):
        result = self._request(json.dumps(answers).encode("utf-8"))
        if not result.get("ok"):
            raise StorageError(result.get("error", "The central sheet rejected the response."))

    def rows(self):
        return [typed_row(row) for row in self._request().get("rows", [])]


class SQLiteStore:
    def __init__(self, path):
        self.path = path
        columns = ", ".join(f"{q['key']} {'TEXT' if q['type'] == 'choice' else 'INTEGER'} NOT NULL" for q in QUESTIONS)
        with self._connect() as conn:
            conn.execute(f"CREATE TABLE IF NOT EXISTS responses "
                         f"(id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, {columns})")

    def _connect(self):
        return sqlite3.connect(self.path)

    def add(self, answers):
        keys = [q["key"] for q in QUESTIONS]
        with self._connect() as conn:
            conn.execute(
                f"INSERT INTO responses (created_at, {', '.join(keys)}) VALUES ({', '.join('?' * (len(keys) + 1))})",
                [datetime.now(timezone.utc).isoformat(timespec="seconds")] + [answers[k] for k in keys])

    def rows(self):
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        try:
            return [typed_row(dict(row)) for row in conn.execute("SELECT * FROM responses ORDER BY id")]
        finally:
            conn.close()

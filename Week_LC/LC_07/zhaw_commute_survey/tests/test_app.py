import csv
import io
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from app import create_app  # noqa: E402
from questions import QUESTIONS  # noqa: E402
from storage import SheetStore, SQLiteStore, StorageError  # noqa: E402

SHEET_URL = "https://example.test/exec"


def valid_form():
    form = {}
    for q in QUESTIONS:
        if q["type"] == "choice":
            form[q["key"]] = q["options"][0]
        elif q["type"] == "likert":
            form[q["key"]] = "4"
    form.update(door_to_door_min="30", distance_km="25", days_per_week="4",
                monthly_cost_chf="80", satisfied="5", age="23")
    return form


def html_of(response):
    return response.get_data(as_text=True)


@pytest.fixture
def client(tmp_path):
    store = SQLiteStore(tmp_path / "test.sqlite")
    return create_app(store).test_client()


def test_form_renders_all_questions(client):
    html = html_of(client.get("/"))
    for q in QUESTIONS:
        assert f'name="{q["key"]}"' in html


def test_valid_submission_shows_commute_per_year(client):
    response = client.post("/", data=valid_form())
    assert response.status_code == 302
    assert "zhaw_commute_submitted=1" in response.headers["Set-Cookie"]
    html = html_of(client.get(response.headers["Location"]))
    assert "112 h" in html  # 30 min * 2 * 4 days * 28 weeks / 60
    assert "CHF 960" in html


def test_missing_and_invalid_answers_are_rejected(client):
    form = valid_form()
    del form["stressful"]
    form["door_to_door_min"] = "-5"
    form["days_per_week"] = "8"
    form["transport"] = "Rocket"
    response = client.post("/", data=form)
    assert response.status_code == 400
    assert "4 question(s) still need an answer" in html_of(response)


def test_csv_export(client):
    client.post("/", data=valid_form())
    text = html_of(client.get("/export.csv"))
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 1
    assert rows[0]["satisfied"] == "5"
    assert rows[0]["monthly_cost_chf"] == "80"


def test_results_page(client):
    # The page also works without any responses
    assert client.get("/results").status_code == 200
    client.post("/", data=valid_form())
    assert "1 response so far" in html_of(client.get("/results"))


@pytest.fixture
def filled_client(client):
    client.post("/", data=valid_form())
    client.post("/", data={**valid_form(), "transport": "Bike or e-bike"})
    return client


def test_sql_console_runs_select(filled_client):
    sql = ("SELECT transport, COUNT(*) AS n FROM responses "
           "GROUP BY transport ORDER BY transport")
    response = filled_client.get("/data", query_string={"q": sql})
    html = html_of(response)
    assert response.status_code == 200
    assert "2 rows" in html
    assert "Bike or e-bike" in html


@pytest.mark.parametrize("sql", [
    "DELETE FROM responses",
    "DROP TABLE responses",
    "ATTACH DATABASE 'x.db' AS x",
    "PRAGMA table_info(responses)",
    "SELECT 1; DELETE FROM responses",
])
def test_sql_console_rejects_non_select(filled_client, sql):
    response = filled_client.get("/data", query_string={"q": sql})
    assert response.status_code == 400
    assert "2 rows" in html_of(filled_client.get("/data"))


def test_sql_console_stops_long_queries(filled_client):
    sql = ("WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
           "SELECT COUNT(*) FROM c")
    response = filled_client.get("/data", query_string={"q": sql})
    assert response.status_code == 400
    assert "took too long" in html_of(response)


def test_sqlite_download_opens_as_database(filled_client, tmp_path):
    path = tmp_path / "download.sqlite"
    path.write_bytes(filled_client.get("/export.sqlite").data)
    conn = sqlite3.connect(path)
    result = conn.execute(
        "SELECT COUNT(*), MAX(door_to_door_min) FROM responses").fetchone()
    conn.close()
    assert result == (2, 30)


# ---- central Google Sheet (Apps Script web app), network replaced by a fake

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


def fake_urlopen(sent, reply):
    def urlopen(request, timeout):
        sent.append(request)
        return FakeResponse(json.dumps(reply).encode())
    return urlopen


def test_sheet_store_posts_answers(monkeypatch):
    sent = []
    monkeypatch.setattr(storage.urllib.request, "urlopen",
                        fake_urlopen(sent, {"ok": True, "id": 1}))
    SheetStore(SHEET_URL).add({"campus": "Zürich", "satisfied": 4})
    assert sent[0].get_method() == "POST"
    assert json.loads(sent[0].data) == {"campus": "Zürich", "satisfied": 4}


def test_sheet_store_types_rows(monkeypatch):
    row = {"id": 1, "created_at": "2026-10-28T10:15:00.000Z",
           "campus": "Zürich", "transport": "Train", "door_to_door_min": 42,
           "distance_km": "15", "days_per_week": 4, "monthly_cost_chf": 220,
           "travel_pass": "GA travelcard", "satisfied": 4, "stressful": 2,
           "productive": 5, "gender": "Female", "age": 22,
           "living": "Shared apartment"}
    monkeypatch.setattr(storage.urllib.request, "urlopen",
                        fake_urlopen([], {"rows": [row]}))
    [typed] = SheetStore(SHEET_URL).rows()
    assert typed["distance_km"] == 15
    assert typed["campus"] == "Zürich"


def test_sheet_errors_are_shown_on_the_form(monkeypatch):
    reply = {"ok": False, "error": "The survey is closed."}
    monkeypatch.setattr(storage.urllib.request, "urlopen",
                        fake_urlopen([], reply))
    client = create_app(SheetStore(SHEET_URL)).test_client()
    response = client.post("/", data=valid_form())
    assert response.status_code == 503
    assert "The survey is closed." in html_of(response)


def test_unreachable_sheet(monkeypatch):
    def fail(request, timeout):
        raise OSError("no network")
    monkeypatch.setattr(storage.urllib.request, "urlopen", fail)
    with pytest.raises(StorageError):
        SheetStore(SHEET_URL).rows()

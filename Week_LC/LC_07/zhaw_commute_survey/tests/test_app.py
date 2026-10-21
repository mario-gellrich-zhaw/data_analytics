import csv
import io
import json
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import storage  # noqa: E402
from app import create_app  # noqa: E402
from questions import QUESTIONS  # noqa: E402
from storage import SheetStore, SQLiteStore, StorageError  # noqa: E402


def valid_form():
    form = {}
    for q in QUESTIONS:
        if q["type"] == "choice":
            form[q["key"]] = q["options"][0]
        elif q["type"] == "likert":
            form[q["key"]] = "4"
    form.update(door_to_door_min="30", distance_km="25", days_per_week="4", monthly_cost_chf="80", satisfied="5",
                age="23")
    return form


@pytest.fixture
def client(tmp_path):
    return create_app(SQLiteStore(tmp_path / "test.sqlite")).test_client()


def test_form_renders_all_questions(client):
    html = client.get("/").get_data(as_text=True)
    for q in QUESTIONS:
        assert f'name="{q["key"]}"' in html


def test_valid_submission_shows_commute_per_year(client):
    response = client.post("/", data=valid_form())
    assert response.status_code == 302
    assert "zhaw_commute_submitted=1" in response.headers["Set-Cookie"]
    html = client.get(response.headers["Location"]).get_data(as_text=True)
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
    assert "4 question(s) still need an answer" in response.get_data(as_text=True)


def test_csv_export(client):
    client.post("/", data=valid_form())
    rows = list(csv.DictReader(io.StringIO(client.get("/export.csv").get_data(as_text=True))))
    assert len(rows) == 1
    assert rows[0]["satisfied"] == "5"
    assert rows[0]["monthly_cost_chf"] == "80"


def test_results_page(client):
    assert client.get("/results").status_code == 200  # also without responses
    client.post("/", data=valid_form())
    assert "1 response so far" in client.get("/results").get_data(as_text=True)


@pytest.fixture
def filled_client(client):
    client.post("/", data=valid_form())
    client.post("/", data={**valid_form(), "transport": "Bike or e-bike"})
    return client


def test_sql_console_runs_select(filled_client):
    response = filled_client.get("/data", query_string={
        "q": "SELECT transport, COUNT(*) AS n FROM responses GROUP BY transport ORDER BY transport"})
    html = response.get_data(as_text=True)
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
    assert filled_client.get("/data", query_string={"q": sql}).status_code == 400
    assert "2 rows" in filled_client.get("/data").get_data(as_text=True)


def test_sql_console_stops_long_queries(filled_client):
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    response = filled_client.get("/data", query_string={"q": sql})
    assert response.status_code == 400
    assert "took too long" in response.get_data(as_text=True)


def test_sqlite_download_opens_as_database(filled_client, tmp_path):
    path = tmp_path / "download.sqlite"
    path.write_bytes(filled_client.get("/export.sqlite").data)
    conn = sqlite3.connect(path)
    assert conn.execute("SELECT COUNT(*), MAX(door_to_door_min) FROM responses").fetchone() == (2, 30)
    conn.close()


# ---- central Google Sheet (Apps Script web app), with the network replaced by a fake

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
    monkeypatch.setattr(storage.urllib.request, "urlopen", fake_urlopen(sent, {"ok": True, "id": 1}))
    SheetStore("https://example.test/exec").add({"campus": "Zürich", "satisfied": 4})
    assert sent[0].get_method() == "POST"
    assert json.loads(sent[0].data) == {"campus": "Zürich", "satisfied": 4}


def test_sheet_store_types_rows(monkeypatch):
    row = {"id": 1, "created_at": "2026-10-28T10:15:00.000Z", "campus": "Zürich", "transport": "Train",
           "door_to_door_min": 42, "distance_km": "15", "days_per_week": 4, "monthly_cost_chf": 220,
           "travel_pass": "GA travelcard", "satisfied": 4, "stressful": 2, "productive": 5,
           "gender": "Female", "age": 22, "living": "Shared apartment"}
    monkeypatch.setattr(storage.urllib.request, "urlopen", fake_urlopen([], {"rows": [row]}))
    [typed] = SheetStore("https://example.test/exec").rows()
    assert typed["distance_km"] == 15
    assert typed["campus"] == "Zürich"


def test_sheet_errors_are_shown_on_the_form(monkeypatch):
    monkeypatch.setattr(storage.urllib.request, "urlopen",
                        fake_urlopen([], {"ok": False, "error": "The survey is closed."}))
    client = create_app(SheetStore("https://example.test/exec")).test_client()
    response = client.post("/", data=valid_form())
    assert response.status_code == 503
    assert "The survey is closed." in response.get_data(as_text=True)


def test_unreachable_sheet(monkeypatch):
    def fail(request, timeout):
        raise OSError("no network")
    monkeypatch.setattr(storage.urllib.request, "urlopen", fail)
    with pytest.raises(StorageError):
        SheetStore("https://example.test/exec").rows()

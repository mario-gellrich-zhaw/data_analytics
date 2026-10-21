"""ZHAW commute survey: a short anonymous questionnaire on how students get
to ZHAW. The responses are explored and analysed in class (chi-squared test,
Spearman correlation).

Students run this app in their own Codespace. All responses go to one
central Google Sheet through an open Apps Script link
(see google_apps_script/Code.gs).

Configuration
- CENTRAL_SHEET_URL below (or the environment variable SHEET_URL): the Apps
  Script web app link. If it is empty, responses are stored in the local
  file responses.sqlite.
"""

import csv
import io
import os
import sqlite3
import statistics
import time
from urllib.parse import urlencode

from flask import Flask, Response, redirect, render_template, request, url_for

from questions import (LIKERT_LABELS, QUESTIONS, SECTIONS,
                       TEACHING_WEEKS_PER_YEAR, commute_per_year)
from storage import (COLUMNS, TEXT_COLUMNS, SheetStore, SQLiteStore,
                     StorageError)

CENTRAL_SHEET_URL = (
    "https://script.google.com/macros/s/"
    "AKfycbwecUh787yY-DxLBazOx-J9LY69JvpRU51NWrJZifoGtaFgslBet5FWHfh1NXoiACrmtQ"
    "/exec"
)

SUBMITTED_COOKIE = "zhaw_commute_submitted"

DEFAULT_QUERY = "SELECT *\nFROM responses\nORDER BY id DESC\nLIMIT 50;"

EXAMPLE_QUERIES = [
    ("Browse responses", DEFAULT_QUERY),
    ("Transport counts",
     "SELECT transport, COUNT(*) AS n\n"
     "FROM responses\nGROUP BY transport\nORDER BY n DESC;"),
    ("Contingency table",
     "SELECT living,\n"
     "       SUM(transport = 'Train')                        AS train,\n"
     "       SUM(transport = 'Tram or bus')                  AS tram_bus,\n"
     "       SUM(transport = 'Car or motorbike')             AS car,\n"
     "       SUM(transport IN ('Bike or e-bike', 'On foot')) AS bike_foot,\n"
     "       COUNT(*)                                        AS total\n"
     "FROM responses\nGROUP BY living;"),
    ("Averages per transport",
     "SELECT transport,\n"
     "       COUNT(*)                         AS n,\n"
     "       ROUND(AVG(door_to_door_min), 1)  AS avg_minutes,\n"
     "       ROUND(AVG(distance_km), 1)       AS avg_km,\n"
     "       ROUND(AVG(monthly_cost_chf), 0)  AS avg_chf_per_month,\n"
     "       ROUND(AVG(satisfied), 2)         AS avg_satisfied\n"
     "FROM responses\nGROUP BY transport\nORDER BY avg_minutes;"),
    ("Minutes vs. satisfaction",
     "SELECT door_to_door_min, satisfied, stressful\n"
     "FROM responses\nORDER BY door_to_door_min;"),
]

# SQL console: only reading statements are authorized on the snapshot
READ_ONLY_ACTIONS = {
    sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
    getattr(sqlite3, "SQLITE_RECURSIVE", 33),
}
MAX_QUERY_ROWS = 500
QUERY_TIMEOUT_S = 2.0


def parse_answers(form):
    """Validate the submitted form.

    Returns (answers, errors), both keyed by question key.
    """
    answers, errors = {}, {}
    for q in QUESTIONS:
        raw = form.get(q["key"], "").strip()
        if q["type"] == "choice":
            if raw in q["options"]:
                answers[q["key"]] = raw
            else:
                errors[q["key"]] = "Please choose one option."
            continue
        if q["type"] == "likert":
            low, high = 1, 5
        else:
            low, high = q["min"], q["max"]
        if raw.isdigit() and low <= int(raw) <= high:
            answers[q["key"]] = int(raw)
        elif q["type"] == "likert":
            errors[q["key"]] = "Please choose a value."
        else:
            errors[q["key"]] = (
                f"Please enter a whole number between {low} and {high}.")
    return answers, errors


def summarize(rows):
    """Aggregates for the results page."""
    n = len(rows)
    likert, choice, number = {}, {}, {}
    for q in QUESTIONS:
        values = [row[q["key"]] for row in rows]
        if q["type"] == "likert":
            counts = [values.count(v) for v in range(1, 6)]
            likert[q["key"]] = [100 * c / n if n else 0 for c in counts]
        elif q["type"] == "choice":
            choice[q["key"]] = [
                (opt, values.count(opt)) for opt in q["options"]]
        elif n:
            number[q["key"]] = {
                "median": statistics.median(values),
                "mean": statistics.fmean(values),
                "min": min(values),
                "max": max(values),
            }
    return {"n": n, "likert": likert, "choice": choice, "number": number}


def build_snapshot(rows):
    """In-memory SQLite copy of the responses for the SQL console and the
    .sqlite download."""
    conn = sqlite3.connect(":memory:")
    columns = ", ".join(
        f"{col} {'TEXT' if col in TEXT_COLUMNS else 'INTEGER'}"
        for col in COLUMNS)
    conn.execute(f"CREATE TABLE responses ({columns})")
    placeholders = ", ".join("?" * len(COLUMNS))
    conn.executemany(
        f"INSERT INTO responses VALUES ({placeholders})",
        [[row[col] for col in COLUMNS] for row in rows])
    conn.commit()
    return conn


def read_only(action, *args):
    """SQLite authorizer: allow reading, deny everything else."""
    if action in READ_ONLY_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def run_read_only_query(conn, sql):
    """Run one SELECT statement.

    Returns (columns, rows, truncated); raises sqlite3.Error.
    """
    conn.set_authorizer(read_only)
    deadline = time.monotonic() + QUERY_TIMEOUT_S
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 1000)
    cursor = conn.execute(sql)
    columns = [d[0] for d in cursor.description] if cursor.description else []
    rows = cursor.fetchmany(MAX_QUERY_ROWS + 1)
    return columns, rows[:MAX_QUERY_ROWS], len(rows) > MAX_QUERY_ROWS


def create_app(store=None):
    app = Flask(__name__)
    sheet_url = os.environ.get("SHEET_URL", CENTRAL_SHEET_URL)
    if store is None:
        if sheet_url:
            store = SheetStore(sheet_url)
        else:
            store = SQLiteStore("responses.sqlite")

    def unavailable(exc):
        return render_template("message.html", title="Data not available",
                               text=str(exc)), 503

    @app.context_processor
    def inject_globals():
        return {"sections": SECTIONS, "likert_labels": LIKERT_LABELS}

    @app.route("/", methods=["GET", "POST"])
    def survey():
        answers, errors, save_error = {}, {}, None
        if request.method == "POST":
            answers, errors = parse_answers(request.form)
            if not errors:
                try:
                    store.add(answers)
                except StorageError as exc:
                    save_error = str(exc)
                else:
                    response = redirect(url_for(
                        "thanks",
                        minutes=answers["door_to_door_min"],
                        days=answers["days_per_week"],
                        cost=answers["monthly_cost_chf"]))
                    response.set_cookie(SUBMITTED_COOKIE, "1",
                                        max_age=60 * 60 * 24 * 90,
                                        samesite="Lax")
                    return response
            answers = request.form
        already = request.cookies.get(SUBMITTED_COOKIE) == "1"
        status = 400 if errors else 503 if save_error else 200
        return render_template("survey.html", answers=answers, errors=errors,
                               save_error=save_error,
                               already=already), status

    @app.route("/thanks")
    def thanks():
        minutes = request.args.get("minutes", type=int)
        days = request.args.get("days", type=int)
        cost = request.args.get("cost", type=int)
        if None in (minutes, days, cost):
            return redirect(url_for("survey"))
        hours, chf = commute_per_year(minutes, days, cost)
        return render_template("thanks.html", hours=hours, chf=chf,
                               minutes=minutes, days=days,
                               weeks=TEACHING_WEEKS_PER_YEAR)

    @app.route("/results")
    def results():
        try:
            rows = store.rows()
        except StorageError as exc:
            return unavailable(exc)
        return render_template("results.html", summary=summarize(rows))

    @app.route("/data")
    def data():
        sql = request.args.get("q", DEFAULT_QUERY).strip()[:5000]
        sql = sql or DEFAULT_QUERY
        try:
            conn = build_snapshot(store.rows())
        except StorageError as exc:
            return unavailable(exc)
        columns, rows, truncated, error = [], [], False, None
        try:
            columns, rows, truncated = run_read_only_query(conn, sql)
        except sqlite3.Error as exc:
            if "interrupted" in str(exc):
                error = "Query took too long and was stopped."
            else:
                error = str(exc)
        finally:
            conn.close()
        examples = [(label, f"?{urlencode({'q': query})}")
                    for label, query in EXAMPLE_QUERIES]
        return render_template("data.html", sql=sql, columns=columns,
                               rows=rows, truncated=truncated, error=error,
                               examples=examples, max_rows=MAX_QUERY_ROWS,
                               schema=QUESTIONS), (400 if error else 200)

    @app.route("/export.csv")
    def export_csv():
        try:
            rows = store.rows()
        except StorageError as exc:
            return unavailable(exc)
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(COLUMNS)
        writer.writerows([row[col] for col in COLUMNS] for row in rows)
        return Response(buffer.getvalue(), mimetype="text/csv", headers={
            "Content-Disposition":
                "attachment; filename=zhaw_commute_responses.csv"})

    @app.route("/export.sqlite")
    def export_sqlite():
        try:
            conn = build_snapshot(store.rows())
        except StorageError as exc:
            return unavailable(exc)
        try:
            payload = conn.serialize()
        finally:
            conn.close()
        return Response(payload, mimetype="application/vnd.sqlite3", headers={
            "Content-Disposition": "attachment; filename=zhaw_commute.sqlite"})

    return app


def public_url(port):
    """Browser address of the app: the forwarded Codespaces URL, or
    localhost outside Codespaces."""
    codespace = os.environ.get("CODESPACE_NAME")
    domain = os.environ.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN",
                            "app.github.dev")
    if codespace:
        return f"https://{codespace}-{port}.{domain}/"
    return f"http://127.0.0.1:{port}/"


if __name__ == "__main__":
    # Print once, not again in the process started by the debug reloader
    if not os.environ.get("WERKZEUG_RUN_MAIN"):
        print(f"\n  Open the survey in your browser: {public_url(5000)}\n")
    create_app().run(host="0.0.0.0", port=5000, debug=True)

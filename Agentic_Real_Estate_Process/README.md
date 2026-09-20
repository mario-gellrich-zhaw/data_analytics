# Agentic Framework Demo: Data Analytics Process Model

A minimal, hand-built agentic framework (no LangChain/CrewAI/AutoGen) presented
as a small live web app &mdash; used as an in-class teaching demo at ZHAW.
**Non-commercial / educational use only.**

Three OpenAI-backed agents &mdash; a **Product Manager**, a **Data Analyst**,
and a **Data Engineer**, peers with no hierarchy between any of them &mdash;
walk through the first four steps of the course's Data Analytics Process
Model (`Week_01`/`Week_02` exercise PDFs, fig. 1), **in order, once, with no
fallback or faked data**:

1. **Business objective** &mdash; fixed, not agent-decided: *build a
   price-prediction model for rental apartments in the canton of Zurich*
   (the course's own Use-Case 1).
2. **Defining appropriate data** &mdash; Product Manager &harr; Data Analyst,
   discussion only (no data tools yet): which real sources, which fields.
3. **Collecting data** &mdash; Product Manager &harr; Data Analyst, now with
   real tools: try scraping a platform (ImmoScout24, Homegate, Comparis,
   Tutti &mdash; one real HTTP request at a time; these all actively guard
   against automated access and return HTTP 403), pivot to the genuinely
   open opendata.swiss API, pick a real dataset/resource, download it.
4. **Preparing & storing data** &mdash; Product Manager &harr; Data Engineer:
   discuss the cleaning/storage approach first, then really do it &mdash;
   profile the raw file (row/column counts, duplicates, missing values),
   clean it, store it in a real local SQLite database, and run a real SQL
   query to verify the storage worked.

**Deliberately no analysis or interpretation happens in this demo** &mdash;
that's the next, not-yet-built part of the process model. Each step's system
prompt explicitly tells the agents to stay on that step's goal and not drift
into later topics (cleaning before data exists, modeling, outlier detection,
statistics); if one agent starts drifting, its peer is instructed to redirect
rather than follow along. The whole run ends after step 4 &mdash; there's no
open-ended "what's next" looping.

All three agents share one growing conversation transcript, so none of them
"forgets" what's already happened (e.g. a platform that was already blocked).
Any of them may optionally sketch a small diagram (plain ASCII or Graphviz
DOT) if it genuinely helps explain something.

A step is a bounded 2-party conversation (Product Manager plus whichever
specialist owns that step) that ends once both sides say, via a status tag,
that it's genuinely done. Click **Stop** at any time to end the run cleanly
(it finishes the current exchange rather than cutting it off mid-sentence); a
generous safety net (80 turns total or 20 minutes) ends it automatically if
nobody does, and any single step that stalls is force-ended after 14 turns.

Nothing is swapped in quietly: every number the agents discuss comes from a
real HTTP request, a real pandas computation, or a real SQL query against the
database that was just created.

## How it works

- `agents.py` &mdash; the framework itself: an `Agent` class with a persona,
  optional tools, and a `speak()` method. Agents don't keep a private
  one-on-one history — `speak()` takes the *shared* transcript (every message
  so far) each turn, so all three always have the full picture. Tool use is
  always `tool_choice="auto"`: the model decides for itself whether to call a
  tool, never forced by the caller. The same class/persona can be
  instantiated twice with different tool sets (e.g. "Data Analyst, no tools
  yet" vs. "Data Analyst, with tools") to make a step's discussion-then-action
  shape happen without any extra machinery — the shared transcript is what
  carries continuity, not the `Agent` object.
- `data_tool.py` &mdash; the real tools, split by who owns them:
  - **Data Analyst** (Collecting data): `attempt_scrape(site)`, one real
    respectful HTTP request per platform (no retries/hammering &mdash; each
    site already said no via robots.txt/ToS); `search_open_data(query)`,
    a real query against opendata.swiss's public CKAN API, returning real
    candidate datasets each with a short resource id (e.g. `r0`) instead of a
    raw URL, so the agent references an id reliably rather than retyping a
    long URL (error-prone — it would sometimes get one slightly wrong or
    invent one); `download_dataset(resource_id)`, which sanity-checks the
    response isn't secretly an HTML page before accepting it as real data.
  - **Data Engineer** (Preparing & storing data): `preview_data(n)`, real
    first rows; `profile_data()`, real row/column/duplicate/missing-value
    counts (profiling *for cleaning*, not analysis); `clean_data(...)`, really
    drops duplicate rows and/or rows missing agent-named key columns, writing
    a real cleaned file; `store_to_database(table_name)`, really writes it
    into a real local SQLite DB (via stdlib `sqlite3` — no new dependency);
    `run_sql_query(query)`, really runs a real read-only `SELECT` (rejects
    anything else) against that DB and returns real rows.
  - **All three agents**: `make_sketch(kind, content, title)` &mdash; hands
    through a diagram the agent authored itself (`"ascii"` or `"dot"`); no
    computation, just structured enough for the UI to render it.
- `server.py` &mdash; a tiny backend (**FastAPI**, not Flask) with one page,
  a Server-Sent-Events endpoint (`/api/stream`) that streams each turn and
  progress update as it happens, and a `POST /api/stop` endpoint for the Stop
  button. A small `run_phase()` helper runs each step's bounded conversation
  (status-tag/min-max-turns mechanism) and is called once per step/sub-step
  in a fixed sequence; `phase_start`/`phase_done`/`turn`/`done` SSE events
  drive the UI.
- `static/` &mdash; a plain HTML/CSS/vanilla-JS frontend (no build step, no
  npm): chat bubbles (Product Manager left; Data Analyst/Data Engineer right,
  in their own colors — they never speak in the same step, so they safely
  share the right-hand slot), inline step dividers, a per-step progress bar,
  a Start/Stop/Run-again button, and full-width data cards (step 3's download
  result, step 4's cleaning/storage/SQL-query/sketch results) dropped inline
  right where each step actually concluded — not collected separately at the
  top or bottom. A "dot"-kind sketch is rendered client-side via a single
  small Graphviz renderer lazily loaded from a CDN only if one actually
  appears; ASCII sketches need no extra loading at all.

## Setup

1. Install dependencies (from the repo root): `pip install -r requirements.txt`
2. Create a `.env` file at the **repository root** with your own key:
   ```
   OPENAI_API_KEY=sk-...
   ```
   This file is already excluded via `.gitignore` &mdash; never commit your key.
3. From this folder, run the server:
   ```
   python server.py
   ```
4. Open <http://localhost:8000> and click **Start conversation**. Click
   **Stop** at any point to end the run.

> [!NOTE]
> A full run makes several real outbound HTTP requests (platform scrape
> attempts, an opendata.swiss search, a real file download) and writes real
> local files (`downloaded_dataset.csv`, `cleaned_dataset.csv`,
> `rental_data.db` — all git-ignored). It consumes your OpenAI API credits
> for the whole duration; typical runs take a few minutes. The demo uses
> `gpt-4o-mini` by default and paces messages ~4s apart so a class can read
> along.

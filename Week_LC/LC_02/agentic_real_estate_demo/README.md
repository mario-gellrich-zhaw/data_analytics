# Agentic Framework Demo: Swiss Real Estate Data

A minimal, hand-built agentic framework (no LangChain/CrewAI/AutoGen) presented
as a small live web app &mdash; used as an in-class teaching demo at ZHAW.
**Non-commercial / educational use only.**

Two OpenAI-backed agents &mdash; a "Data Researcher" and a "Data Source Expert"
&mdash; work through the real data-sourcing workflow live, with **no fallback
or faked data, and no scripted sequence of steps**: the expert agent decides
for itself, turn by turn, whether and which real tool to use next. A typical
run looks like this, but nothing about the order is hard-coded:

1. They discuss how to get Swiss rental apartment data.
2. The expert really tries to scrape a platform (ImmoScout24, Homegate,
   Comparis, or Tutti) &mdash; one real HTTP request at a time, deciding
   itself whether to try another if one is blocked (these platforms
   actively guard against automated access; live requests return HTTP 403).
3. It pivots to a genuinely open, public API &mdash; opendata.swiss &mdash;
   and picks a real dataset/resource from what it actually finds.
4. It really **downloads** that resource.
5. It really **analyzes** the file with pandas (row count, duplicate rows,
   missing values, real column names) and checks &mdash; itself &mdash;
   whether it actually contains rental *prices*, not just counts. If not,
   it searches again with different terms instead of settling.
6. It really **previews** the first rows when asked to show what the data
   looks like.

Both agents share one growing conversation transcript, so neither "forgets"
what's already been tried (e.g. a platform that was already blocked) — this
also means the conversation ends dynamically, once both agents say, in their
own words, that they're satisfied (bounded by a safety cap so a live demo
can't run forever).

Nothing is swapped in quietly: every number the agents discuss comes from a
real HTTP request or a real pandas computation on the file that was just
downloaded.

## How it works

- `agents.py` &mdash; the framework itself: an `Agent` class with a persona,
  optional tools, and a `speak()` method. Crucially, agents don't keep a
  private one-on-one history — `speak()` takes the *shared* transcript (every
  message from both agents so far) each turn, so both always have the full
  picture. Tool use is always `tool_choice="auto"`: the model decides for
  itself whether to call a tool, never forced by the caller.
- `data_tool.py` &mdash; the five real tools the expert agent can call:
  - `attempt_scrape(site)` &mdash; one real, respectful HTTP request to one
    platform the agent picks (no retries/hammering &mdash; each site already
    said no via robots.txt/ToS).
  - `search_open_data(query)` &mdash; a real query against opendata.swiss's
    public CKAN API. Returns real candidate datasets, each resource tagged
    with a short id (e.g. `r0`) &mdash; short ids instead of raw URLs, so the
    agent picks *which* one by referencing an id (reliable) rather than
    having to retype a long URL exactly (error-prone; it would sometimes get
    URLs slightly wrong or invent one).
  - `download_dataset(resource_id)` &mdash; really downloads the resource the
    agent picked; sanity-checks that the response isn't secretly an HTML
    page before accepting it as real data.
  - `analyze_data()` &mdash; really loads the file with pandas and computes
    row count, column count, duplicate rows, missing values per column, and
    the real column names.
  - `preview_data(n)` &mdash; really reads the first N rows of the downloaded
    file (the agent decides N).
- `server.py` &mdash; a tiny backend (**FastAPI**, not Flask) with one page
  and one Server-Sent-Events endpoint (`/api/stream`) that streams each
  conversation turn and progress update to the browser as it happens, and
  runs the whole conversation as a single loop that ends once both agents
  independently signal (via a short status tag in their own reply, stripped
  before display) that they're satisfied — or a safety cap is hit.
- `static/` &mdash; a plain HTML/CSS/vanilla-JS frontend (no build step, no
  npm): chat bubbles (left = Data Researcher, right = Data Source Expert, two
  colors only &mdash; a small "real action" label marks a real tool call
  without changing the bubble color), a live progress bar, and a results
  panel with the real dataset source, download size, structure/quality
  stats, and a table of the first rows.

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
4. Open <http://localhost:8000> and click **Start conversation**.

> [!NOTE]
> Each run calls the OpenAI API repeatedly and makes several real outbound
> HTTP requests (platform scrape attempts, opendata.swiss searches, a real
> file download, row-preview reads) &mdash; exactly which and how many
> depends on what the agents decide to do. It consumes your OpenAI API
> credits and typically takes 1&ndash;3 minutes end to end. The demo uses
> `gpt-4o-mini` by default and paces messages ~4s apart so a class can read
> along. `downloaded_dataset.csv` is generated at runtime and is git-ignored.

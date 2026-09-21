# Agentic Framework Demo: Data Analytics Process Model

An experiment from the **Data Analytics** module at ZHAW: a small, hand-built
agentic framework (no LangChain/CrewAI/AutoGen) wrapped in a live web app,
used to explore how multiple LLM-based agents can collaborate on a real
data task. **Non-commercial / educational use only.**

Three OpenAI-backed agents &mdash; a **Product Manager**, a **Data Analyst**,
and a **Data Engineer**, peers with no hierarchy between them &mdash; work
through the first steps of the course's Data Analytics Process Model:
agreeing on a business objective, defining what data is needed, actually
collecting it (real web scraping / open-data API calls), and preparing and
storing it (real cleaning, a real SQLite database, a real SQL query). No
analysis or modeling happens yet &mdash; that's a later part of the process
model this demo doesn't cover.

All agents share one growing conversation transcript, decide for themselves
whether and when to use their tools, and every number they discuss comes
from a real request, computation, or query &mdash; nothing is faked or
pre-scripted. A run ends automatically once the steps are done, or any time
via the **Stop** button.

## How it works

- `agents.py` &mdash; a minimal `Agent` class (persona + optional tools +
  shared transcript) that is the whole framework.
- `data_tool.py` &mdash; the real tools the agents can call: scraping,
  open-data search/download, data profiling/cleaning, database storage, and
  a small sketching tool.
- `server.py` &mdash; a small FastAPI backend that orchestrates the agent
  conversation and streams it to the browser via Server-Sent Events.
- `static/` &mdash; a plain HTML/CSS/JS frontend (no build step) that renders
  the conversation as a chat, with live progress and result cards.

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
> A full run makes real outbound HTTP requests, writes real local files
> (git-ignored), and consumes your OpenAI API credits for its duration —
> typically a few minutes.

"""Start the demo's web server: `python main.py`, then open
http://localhost:8000.

The app itself lives in the packages next to this file:

  app/        FastAPI server, one run's orchestration, all settings
  agents/     prompts, personas, and the LangGraph turn-taking graph
  tools/      the real tools the agents call (+ per-run state, checks, schemas)
  sandbox/    scraper_kit.py — agent-written scrapers' only way to the web
  reporting/  saves each run as Markdown + HTML
"""

import uvicorn

from app.server import app

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)

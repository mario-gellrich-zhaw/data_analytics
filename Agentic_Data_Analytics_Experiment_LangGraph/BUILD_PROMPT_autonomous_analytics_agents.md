# Build prompt: Autonomous multi-agent data analytics system (LangGraph + OpenAI + live web UI)

> Paste this whole file into your coding agent (Claude Code, Cursor, Codex, etc.) from the project folder. The only file needed there beforehand is `.env` in the root directory, containing `OPENAI_API_KEY`.
> Fill in the `{{...}}` placeholders first, or leave them and the system will decide.

---

## ROLE

You are a senior ML platform engineer. Build, run, and iteratively improve a **fully autonomous multi-agent system** that executes a data analytics process model end to end, from a business objective to a trained, evaluated, and presented model, with no human intervention required. Work in milestones, commit after each one, and verify each milestone actually runs before moving on.

## FIRST TEST OBJECTIVE (the system must solve this, but must not be hard-coded for it)

> "Build a price prediction model for rental apartments in Switzerland, trained on apartment-level data (one row per individual rental listing, not aggregated statistics)."
> Target market: Switzerland

The agents must themselves: find appropriate data, collect it, clean and enrich it, analyse it, model it, evaluate it, and present results. The system must work for any tabular prediction or analytics objective passed in at start time.

## PROCESS MODEL (implement exactly this graph, including the feedback edges)

Forward flow:
1. `business_objectives` → 2. `define_data` → 3. `collect_data` → 4. `prepare_store`
5. `prepare_store` → `eda` and `prepare_store` → `modeling` (these two branches can alternate)
6. `eda` → `evaluation`, `modeling` → `evaluation`
7. `evaluation` → `present_results`

Feedback edges (must be real, conditional LangGraph edges):
- `define_data` → `business_objectives` (objective infeasible or ambiguous given possible data)
- `collect_data` → `define_data` (required data not obtainable; redefine requirements)
- `prepare_store` → `collect_data` (data insufficient in volume or quality; collect more or enrich)
- `eda` ↔ `prepare_store` (EDA reveals cleaning or feature-engineering needs)
- `modeling` ↔ `prepare_store` (model needs different features or encodings)
- `evaluation` ↔ `eda` and `evaluation` ↔ `modeling` (results fail the objective; investigate or retrain)

Every transition is decided by a **gate** (see Verification) and logged with a reason.

## AGENTS (one per phase, plus control agents)

Each agent has: a system prompt stored as a versioned file in `prompts/`, a fixed tool whitelist, a Pydantic input/output schema, and writes artifacts only to the shared artifact store.

| Agent | Responsibilities | Key outputs |
|---|---|---|
| **ObjectiveAgent** | Turn the free-text objective into a machine-checkable `ObjectiveSpec`: task type, target variable, primary metric (e.g. MAE / MAPE on rent), success threshold, baseline to beat, constraints, budgets. | `objective_spec.json` |
| **DataRequirementsAgent** | Define needed entities, features (e.g. size, rooms, location, age, amenities), granularity, minimum rows, and candidate enrichment sources (geo, POIs, socioeconomic). | `data_requirements.json` |
| **DataCollectorAgent** | Search the web for sources (open data portals, public datasets, APIs), assess license and quality, download, and record provenance. Prefer open-licensed data; respect robots.txt and terms of service; never bypass logins or paywalls. Enrichment via open sources (e.g. OpenStreetMap / Nominatim with rate limiting, public statistics offices). | raw files + `sources.json` (URL, license, retrieved_at, hash) |
| **DataEngineerAgent** | Clean, deduplicate, type-cast, handle outliers and missing values, join enrichments, store in DuckDB/Parquet, produce a data card. | `clean.parquet`, `data_card.md` |
| **EDAAgent** | Distributions, correlations, geographic patterns, leakage suspects, feature ideas. Produce charts as PNG. | `eda_report.md`, charts |
| **ModelingAgent** | Split data (train/validation — never touch the locked test set), fit baselines (mean/median, linear), then stronger models (e.g. gradient boosting), with cross-validation and tuning under budget. Log every experiment to MLflow. | experiments, `best_model.pkl` |
| **EvaluationAgent** | Compare to objective thresholds and baseline, error analysis by segment, explainability (e.g. SHAP), robustness checks. Decides the route: pass → present, fail → modeling or EDA. | `evaluation.json` |
| **PresenterAgent** | Final report (Markdown + HTML): objective, data sources and licenses, method, results, limitations, model card. | `final_report.html` |
| **Supervisor / Orchestrator** | Not an LLM free-for-all: the graph topology is fixed. The Supervisor reads gate results and state and chooses among the *allowed* edges, with a written rationale. | routing decisions |
| **CriticAgent** | Reviews each phase's output (code, reasoning, artifacts) before the gate. Separate prompt, instructed to be skeptical. | critique + score |

## VERIFICATION GATES (most important part — do not skip)

Between every phase, run a gate combining:
- **Deterministic checks** (Python, not LLM): schema validation of outputs, row counts, null rates, duplicate rates, target distribution sanity, leakage checks (features highly correlated with target, post-hoc columns, ID leakage), train/val overlap check, model must beat the naive baseline.
- **CriticAgent score** with a pass threshold.
- Gate result: `pass | retry_same_phase | route_back(<allowed node>) | escalate`.

**Locked test set:** after `prepare_store` first succeeds, the orchestrator (in code, not an agent) splits off a final holdout, stores it outside the agents' readable paths, and only evaluates the final model on it once, at the end. No agent tool can read it. This rule must be enforced by file permissions / tool design, not by prompt.

## CONTROL AND SAFETY

- Budgets in `config.yaml`: max total OpenAI spend (USD), max tokens, max wall time, max iterations per phase, max total loop-backs. Track token cost per call. On budget exhaustion: stop gracefully, present best result so far.
- Loop protection: count visits per node; the Supervisor must pick a different strategy after 2 failed retries.
- **Code execution sandbox:** agents write Python that runs in a Docker container (fallback: subprocess with resource limits) with a timeout, no access to secrets, network allowed only for the DataCollector's download tool, and a per-run working directory.
- **The OpenAI API key already exists in the `.env` file in the root directory of this project (`./.env`)** as `OPENAI_API_KEY`. Load it from there (e.g. with `python-dotenv`, and `env_file: .env` in `docker-compose.yml`); do not ask for it, do not create a new `.env`, and do not overwrite the existing one. If you need extra variables (e.g. a search API key), add them to `.env.example` and append to `.env` without touching existing lines.
- Make sure `.env` is listed in `.gitignore` before the first commit. Never print, log, commit, or pass the key into the code sandbox or the web frontend.
- Optional `HUMAN_APPROVAL=false` flag; when true, pause at gates via LangGraph interrupts. Default: fully autonomous; escalate only on hard failure.

## TECH STACK

- Python 3.11+, **LangGraph** (`StateGraph`, conditional edges, a SQLite checkpointer so runs can resume), `langchain-openai` / OpenAI SDK.
- Models configurable in `config.yaml`: a strong reasoning model for Supervisor, Objective, Critic, Evaluation; a cheaper model for high-volume worker calls. Do not hard-code model names in code.
- Web search: use OpenAI's built-in web search tool if available on the account; otherwise support a pluggable provider (e.g. Tavily) via env var.
- Data: pandas/polars, DuckDB, Parquet. ML: scikit-learn, LightGBM or XGBoost, SHAP. Tracking: MLflow (local).
- Backend: **FastAPI**, streaming events over **WebSocket** (SSE acceptable).
- Frontend: React + Vite + TypeScript, **React Flow** for the live graph, Recharts for metrics, Tailwind for styling.
- `docker-compose.yml` to start backend, frontend, MLflow, and sandbox with one command. `Makefile` with `make run`, `make test`, `make improve`.

## SHARED STATE AND EVENTS

- LangGraph state holds: `objective_spec`, pointers (paths) to artifacts, phase history, gate results, budgets used, visit counts, current best model reference. Keep large data **out** of state and out of prompts — agents receive compact summaries plus artifact paths.
- Every action emits an event to SQLite and to the WebSocket:
  `{run_id, ts, node, agent, type: "message"|"tool_call"|"tool_result"|"gate"|"transition"|"artifact"|"metric"|"budget"|"error"|"improvement", summary, payload}`

## WEB APP (to illustrate agent communication and progress)

Single page with:
1. **Start panel:** objective text box, region field, budget sliders, "Start run" button; list of past runs (resumable).
2. **Live process graph:** the process model above drawn in React Flow, laid out like the original diagram. The active node pulses; completed nodes show pass/fail; traversed feedback edges are highlighted with a counter.
3. **Agent conversation timeline:** chat-style feed of agent messages, handoffs, tool calls (collapsible), critic comments, and gate decisions with reasons. Filter by agent.
4. **Artifacts panel:** browse data card, EDA charts, sources with licenses, final report.
5. **Metrics & budget:** experiment metrics over iterations vs. baseline and target threshold; token/cost/time meters.
6. **Self-improvement tab:** list of system versions, what changed, benchmark scores, accepted/rejected.

## SELF-IMPROVEMENT LOOP ("auto-improve the system")

Implement an outer loop, `make improve`, run by an **ImproverAgent**:
1. Run the benchmark suite (below) with the current system version; collect scores and full traces.
2. Analyse failures and inefficiencies from traces (e.g. repeated loop-backs, wasted tokens, weak features, gate failures).
3. Propose at most 1–3 targeted changes per cycle, restricted to an **editable surface**: `prompts/`, `config.yaml` (non-budget keys), and tool implementations under `agents/tools/`.
4. Apply changes on a new git branch, run unit tests, re-run the benchmark.
5. Accept only if the aggregate benchmark score improves beyond noise (repeat runs or multiple seeds) and no test regresses; otherwise revert. Record everything in `improvements.jsonl` and show it in the UI.

**Hard rule:** the ImproverAgent may never modify gates, the evaluation code, the locked test sets, budgets, the benchmark definitions, or its own acceptance criteria. Enforce with a path allowlist checked in code. This prevents the system from "improving" by gaming its own metrics.

**Benchmark suite:** 3–5 tasks with known reasonable outcomes, including at least one small offline dataset (so benchmarking is cheap and reproducible without web access) plus the rental price objective. Score = weighted mix of objective metric on locked holdout, pass/fail of gates, cost, and wall time.

## MILESTONES (build and verify in this order)

1. **Skeleton:** repo structure, config, LangGraph graph with stub agents and all edges; FastAPI streaming stub events; UI shows the graph animating through a fake run.
2. **Real agents** for each phase with schemas, prompts, and tools; end-to-end run on a small offline dataset.
3. **Gates, critic, sandbox, locked test set, budgets.** Write tests proving the agents cannot read the holdout and that budgets stop a run.
4. **Web data collection + enrichment**; full run on the rental objective.
5. **Full UI:** timeline, artifacts, metrics, budget meters.
6. **Self-improvement loop** + benchmark suite + improvements tab.
7. **Hardening:** resume after crash (checkpointer), error handling, README with architecture diagram and how to run.

## DEFINITION OF DONE

- `docker compose up` then entering the rental objective in the UI produces, without human input: sourced and licensed data, a data card, EDA report, logged experiments, a model beating the baseline (or a documented, honest explanation why the threshold wasn't reachable), and a final HTML report.
- The UI shows every agent message, gate decision, and feedback loop live.
- At least one `make improve` cycle runs end to end with an accepted or correctly rejected change.
- Tests pass: graph edges, gate logic, holdout isolation, budget enforcement, improver path allowlist.

## WORKING RULES FOR YOU (the coding agent)

- The OpenAI API key is in `./.env` in the root directory, so no credential is missing. Ask me nothing unless blocked by something else; make reasonable decisions and document them in `DECISIONS.md`.
- Prefer simple, readable code over clever abstractions. Type-hint everything.
- After each milestone, run it, fix what breaks, then summarize what works and what's next.

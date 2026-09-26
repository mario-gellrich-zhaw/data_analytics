# Decisions

Decisions made while building the system without asking, with the reason for each.

## Environment

- **Where `.env` lives.** The spec expects `./.env`. In this repository the key is in the repository root (`../.env`). `ada/config.py` loads the nearest `.env` walking up from the project directory and never overrides variables that are already set. `docker-compose.yml` lists both paths as optional `env_file`s. No new `.env` was created.
- **`.env` permissions.** The repo-root `.env` was world-readable (`666`). It is now `600`, so the sandbox user cannot read the key. Its content is unchanged.
- **Separate virtualenv (`.venv/`).** Installing MLflow into the shared course environment upgraded `protobuf` to 6.x, which breaks the course's pinned `tensorflow==2.17.0`. That change was reverted (`protobuf==4.25.8`, `pip check` clean). The project now has its own `.venv` and `requirements.txt`.
- **No Docker in the dev container.** `docker` isn't installed and user namespaces are blocked (`unshare` fails), so neither Docker nor bubblewrap can be used here. The sandbox has four modes (see below). Docker/compose files are provided but couldn't be tested in this environment.
- **Default ACLs removed on the project folder.** The workspace mount gave every new file `rw-rw-rw-` through a default ACL, which meant sandboxed code could rewrite gate code. `setfacl -R -b` + `chmod -R go-w` on this project only; new files are now `644`.
- **Git.** Work happens on the branch `agentic-v2` of the existing `data_analytics` repository. Only this folder is staged. The user's uncommitted changes in other folders are left alone.

## Models and cost

- **Models:** strong = `gpt-5.1`, worker = `gpt-5-mini`, reasoning effort `low`, all set in `config.yaml` (`models`, `roles`). They were picked because their list prices are known (`pricing_usd_per_1m`), which keeps budget tracking accurate. Newer models on the account (`gpt-5.4`, `gpt-5.5`, …) can be switched in by editing config; their prices then need to be added to the pricing table.
- **OpenAI SDK directly (Responses API)** rather than LangChain chat wrappers. This gives exact token/cached-token usage per call for cost tracking, and lets the built-in `web_search` tool be used. LangGraph still orchestrates.
- **Web search:** OpenAI built-in `web_search` by default; `web.search_provider: tavily` + `TAVILY_API_KEY` switches to Tavily.

## Architecture

- **Phase = one LangGraph node.** Inside the node: agent work → deterministic checks → critic → gate decision → supervisor picks one of the gate-allowed edges → conditional edge. Feedback edges are real `add_conditional_edges` targets. The path map of every node contains only its forward, feedback, retry (self) and escalation (`present_results`) targets.
- **Retry and escalation edges.** `retry_same_phase` needs a self-edge. `escalate` and budget exhaustion go to `present_results` ("stop gracefully, present best result so far"). These are the only edges not drawn in the process model.
- **EDA ↔ modeling alternation.** After an EDA pass the supervisor may choose `evaluation` or `prepare_store` (to apply EDA findings). From `prepare_store` it may choose `eda` or `modeling`. Evaluation without a model routes to modeling deterministically, without spending tokens.
- **Supervisor only chooses when there is a choice.** If the gate leaves exactly one option, routing is deterministic (logged with the gate's reason). The supervisor LLM is called only for real choices, and its answer is validated against the allowed options.
- **Critic is skipped when deterministic checks already failed.** A hard failure means a retry anyway, so the critic's tokens would be wasted.
- **"Different strategy after 2 failed retries"**: `budgets.max_failed_retries: 2`. A third consecutive failure forbids `retry_same_phase`. The gate then offers only upstream feedback edges. If only the critic objects and all deterministic checks pass, the gate continues with warnings. If neither applies, it escalates.
- **Phase work is cached per visit** (`.cache/<node>_<visit>.json`). Resuming after a crash or a human-approval interrupt doesn't repeat finished agent work, because LangGraph re-executes an interrupted node from its start.

## Holdout and isolation

- **Hash-based split.** The holdout (20%) and the validation split (20% of the rest) are pure functions of a salted SHA-256 of `_row_id`. After a later rebuild of the clean data, the same entities stay in the holdout. The engineer must make `_row_id` stable (derived from source identity).
- **Lock timing.** Per spec, the holdout is locked when `prepare_store` first passes its gate. From then on, the orchestrator strips holdout rows from every table under `clean/ data/ models/ eda/ evaluation/` after each sandboxed script. Before the lock, the data engineer's own scripts see all rows. That is unavoidable, because cleaning happens on the raw data. The prompt limits `prepare_store` to row-level operations; statistics-based imputation/encoding belong in the modeling pipeline, which is fit on train only.
- **Enforcement is by file permissions and tool design, not by prompt:**
  1. The vault is `~/.ada_vault/<run_id>/` (mode `0700`, backend user), outside the workspace.
  2. Every agent file tool resolves paths through `RunStore.resolve`, which rejects anything outside the workspace.
  3. Agent code runs as a different OS user (`adasandbox`) that cannot read the vault or `.env`.
  4. A `sitecustomize` audit hook in the sandbox also blocks the vault path, network, writes outside the workspace and spawning non-Python programs (defence in depth).
- **Final holdout evaluation happens exactly once** (`evaluated.json` marker). The model pickle is agent-produced code, so it is unpickled only inside the sandbox. It receives the holdout features without the target; metrics are computed outside.
- **Validation split is tamper-proof.** `data/splits.json` is recomputed from the hash rule before modeling and before validation, so an agent editing it has no effect.
- **Sandbox modes:** `docker` (network none, only the workspace mounted), `remote` (the compose `sandbox` service on an internal network with no egress), `subprocess-user` (local, `sudo setpriv` to `adasandbox`, rlimits, `timeout -s KILL`), `subprocess-same-user` (refused unless `sandbox.require_isolation: false`).
- **Network for data collection** is only available to the DataCollector's tools, which run in the backend process: `web_search`, `fetch_url`, `download_file`, `geocode_addresses`, `osm_poi_counts`. Sandboxed code has no network. In local subprocess mode, the no-network guarantee comes from the audit hook only, because iptables isn't available in this container. Docker and compose modes enforce it at the network level.

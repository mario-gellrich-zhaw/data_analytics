# Autonomous data analytics agents — common tasks
VENV ?= .venv
PY := $(VENV)/bin/python
SANDBOX_USER ?= adasandbox
PORT ?= 8000
MLFLOW_PORT ?= 5000

.PHONY: setup sandbox-user frontend run dev demo test improve bench mlflow clean-runs

setup: ## venv + python deps + frontend deps
	test -d $(VENV) || python3 -m venv $(VENV)
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt
	cd frontend && npm install --no-fund --no-audit

sandbox-user: ## local sandbox isolation: a separate OS user for agent code (needs sudo)
	id $(SANDBOX_USER) >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin $(SANDBOX_USER)
	@echo "agent code will run as $(SANDBOX_USER); keep secrets (.env) mode 600"

frontend: ## build the React app (served by the backend on :$(PORT))
	cd frontend && npm run build

run: frontend ## backend + UI on :$(PORT), MLflow UI on :$(MLFLOW_PORT)
	@mkdir -p var
	@echo "UI: http://localhost:$(PORT)   MLflow: http://localhost:$(MLFLOW_PORT)"
	@trap 'kill 0' EXIT; \
	$(VENV)/bin/mlflow ui --backend-store-uri sqlite:///$(CURDIR)/var/mlflow.db --port $(MLFLOW_PORT) > var/mlflow.log 2>&1 & \
	$(VENV)/bin/uvicorn ada.api.server:app --host 0.0.0.0 --port $(PORT)

dev: ## backend with reload + Vite dev server on :5173
	@trap 'kill 0' EXIT; \
	$(VENV)/bin/uvicorn ada.api.server:app --reload --port $(PORT) & \
	cd frontend && npm run dev

demo: ## scripted run without LLM calls (animates the UI)
	$(PY) -m ada.cli run --stub

test: ## unit tests (no LLM calls)
	$(PY) -m pytest

improve: ## one self-improvement cycle (benchmark -> propose -> branch -> re-benchmark -> accept/reject)
	$(PY) -m improve.loop

bench: ## run the benchmark suite once with the current system version
	$(PY) -m improve.benchmark

mlflow:
	$(VENV)/bin/mlflow ui --backend-store-uri sqlite:///$(CURDIR)/var/mlflow.db --port $(MLFLOW_PORT)

clean-runs: ## delete local run data (not the improvement log)
	rm -rf var/runs var/*.db var/*.db-* var/mlartifacts

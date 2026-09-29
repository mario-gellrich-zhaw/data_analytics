# Executes agent-written code. Mounted: only the runs volume. No vault, no .env, no internet (internal network).
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ada ada
RUN useradd --uid 10001 --create-home sandbox
USER 10001
ENV PYTHONUNBUFFERED=1 MPLBACKEND=Agg ADA_RUNS_DIR=/data/var/runs
EXPOSE 8100
CMD ["uvicorn", "ada.sandbox.runner_service:app", "--host", "0.0.0.0", "--port", "8100"]

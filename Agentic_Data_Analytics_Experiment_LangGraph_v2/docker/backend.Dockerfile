FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY ada ada
COPY agents agents
COPY improve improve
COPY benchmarks benchmarks
COPY datasets datasets
COPY prompts prompts
COPY tests tests
COPY config.yaml pytest.ini improvements.jsonl* ./
ENV PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["uvicorn", "ada.api.server:app", "--host", "0.0.0.0", "--port", "8000"]

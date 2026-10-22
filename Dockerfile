# One image for local compose and Aiven Runtime (docs/DEPLOY.md). Runtime reads EXPOSE for the HTTP port.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml ./
COPY app app
COPY workers workers
COPY vision vision
COPY agents agents
COPY eval eval
COPY db db
RUN pip install --no-cache-dir ".[vision,llm]"
COPY scripts scripts
COPY deploy deploy
COPY docs/api-mock.json docs/api-mock.json
ENV PORT=8000
EXPOSE 8000
# API (uvicorn) + aggregation worker thread (+ SYNTHETIC demo loop when DEMO_SYNTHETIC=1)
CMD ["python", "-m", "deploy.runtime"]

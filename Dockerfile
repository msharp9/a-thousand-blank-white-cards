# syntax=docker/dockerfile:1
FROM python:3.14-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl \
  && rm -rf /var/lib/apt/lists/*

# Install uv
RUN pip install --no-cache-dir uv

WORKDIR /app

# Copy dependency manifests + source for a locked, reproducible install.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY data/ ./data/

# Install into a project venv from the lockfile (runtime deps only, no dev group).
RUN uv sync --frozen --no-dev

ENV PORT=8000
EXPOSE 8000

# uv run uses the synced venv; bind to 0.0.0.0 and honor the platform's $PORT.
CMD ["sh", "-c", "uv run uvicorn board.app:app --host 0.0.0.0 --port ${PORT:-8000}"]

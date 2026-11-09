# Runtime image for the FastAPI backend.
# Build from the repository root:  docker build -f backend/Dockerfile -t ai-workspace-api .
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# ffmpeg is optional (video tooling); libgl/libgomp are needed by some imaging deps.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libgl1 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --upgrade pip && pip install -r /app/backend/requirements.txt

COPY backend /app/backend
ENV PYTHONPATH=/app:/app/backend

# Non-root user; the SQLite file lives in a mounted volume at /data.
RUN useradd --create-home --uid 10001 appuser && mkdir -p /data && chown -R appuser:appuser /data /app
USER appuser

ENV DATABASE_URL=sqlite:////data/app.db
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/v1/health || exit 1

# Single worker keeps the in-process rate limiter and quota ledger consistent.
# Scale horizontally only behind a shared limiter (see docs/DEPLOYMENT.md).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--proxy-headers", "--forwarded-allow-ips", "*"]

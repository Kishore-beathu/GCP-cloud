# Backend container for Cloud Run.
#
# Two stages so build tooling (compilers for asyncpg wheels) never ships in the
# runtime image.

FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Compilers are needed only if a dependency lacks a manylinux wheel.
RUN apt-get update \
    && apt-get install --no-install-recommends -y build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt


FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Run as a non-root user; Cloud Run does not require root and this limits blast
# radius if the process is ever compromised.
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser data ./data
COPY --chown=appuser:appuser db ./db

USER appuser

# Cloud Run injects PORT and expects the server to listen on it.
ENV PORT=8080
EXPOSE 8080

# One worker per container: the scheduler must not run in duplicate, and Cloud
# Run scales by adding instances rather than in-process workers. If you scale
# past one instance, set SCHEDULER_ENABLED=false on all but a dedicated job
# service (see docs/DEPLOYMENT.md).
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1

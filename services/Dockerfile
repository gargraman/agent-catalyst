# Agent Catalyst API — dev/pilot container image (deployment infra slice).
# Single-stage slim image; M11 hardens it (multi-stage/distroless, pinned base digest, resource limits).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# First-party source (packages declared in pyproject setuptools `include`) + Alembic assets for migrations.
# `migrations` is NOT an installed package — it is run from disk via alembic.ini (script_location = migrations).
COPY pyproject.toml alembic.ini ./
COPY api ./api
COPY runtime ./runtime
COPY evals ./evals
COPY migrations ./migrations

# Install the API extra (fastapi, uvicorn, sqlalchemy, asyncpg, psycopg, alembic, redis, litellm,
# prometheus-client, pyjwt[crypto]) + the first-party packages (api/runtime/evals) into site-packages.
RUN pip install ".[api]"

# Drop privileges — never run the service as root.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Liveness: GET /healthz (no deps). Readiness (orchestration probe): GET /readyz (checks the DB).
# NOTE: auth is fail-closed by default (feature 012) — a real deployment MUST set AC_ENV + AC_AUTH_PROVIDER
# (+ IAM config for trellix-iam). The dev compose sets AC_ENV=dev/AC_AUTH_PROVIDER=dev.
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

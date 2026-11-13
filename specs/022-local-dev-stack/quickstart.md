# Quickstart — local dev stack (feature 022)

Boot the backend stack locally and run its integration tests. All credentials here are **dev-only dummies**.
Object storage is **AWS S3**, emulated locally by **LocalStack S3** (same emulator/endpoint as secrets).

## Prerequisites
- Docker + Docker Compose v2 (`docker compose`, not the legacy `docker-compose`).
- For the integration tests: the `services` Python env with `boto3` (the `api` extra) + `pytest` (the `test`
  extra). Either activate `services/.venv` or pass `PYTEST=...` to `make` (see below).

## Bring up the stack
```bash
make up            # Postgres+pgvector, PgBouncer, Redis, LocalStack (secrets + S3, buckets created), api
make logs          # follow logs
make down          # stop (named volumes kept; `docker compose -f deploy/compose.yaml down -v` wipes data)
```
- API → http://localhost:8000 (`/healthz`, `/readyz`)
- LocalStack edge (Secrets Manager + KMS + S3) → http://localhost:4566
- Buckets created on LocalStack-ready by `deploy/localstack/init-s3.sh`: `ac-uploads` (app object store),
  `langfuse` (observability substrate). Re-create them on a running stack with `make bootstrap` (idempotent).
- Inspect S3 locally, e.g.: `aws --endpoint-url http://localhost:4566 s3 ls` (dummy creds `test`/`test`).

## Run the integration tests
These are **opt-in** (skipped in the normal unit run). The target brings up `localstack`, then runs **both**
the secrets and S3 tests against the live stack:
```bash
make test-integration
# venv not on PATH? e.g.:
make test-integration PYTEST="./services/.venv/bin/python -m pytest"
```
Selection is by the `AC_RUN_LOCALSTACK=1` env gate (not `pytest -m integration`), so the secrets roundtrip
(`test_aws_secrets_localstack.py`) and the S3 bucket/roundtrip (`test_s3_buckets_localstack.py`) both run.

The plain unit suite is unaffected and stays infra-free:
```bash
cd services && python -m pytest        # no AC_RUN_LOCALSTACK → the integration tests skip
```

## Observability (PLACEHOLDER — not yet functional)
Langfuse + ClickHouse exist behind a disabled compose profile as a **thin placeholder** — they are **not
Langfuse-v3-ready** and are not expected to come up green as-is (see `deploy/compose.yaml` comments and
`docs/backend/11`). Validate the profile only parses:
```bash
make observability-config      # asserts `docker compose --profile observability config` is valid
# make observability-up        # starts the placeholder — will NOT work until the real v3 topology is built
```

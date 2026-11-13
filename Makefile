# Agent Catalyst — local dev stack (feature 022). Thin wrapper over `docker compose` + the opt-in integration
# tests. Targets the dev compose at deploy/compose.yaml. All creds here are dev-only dummies.
COMPOSE := docker compose -f deploy/compose.yaml
# Host-side pytest runner. Override if you don't have the services venv on PATH, e.g.:
#   make test-integration PYTEST="./services/.venv/bin/python -m pytest"
PYTEST ?= python -m pytest

.PHONY: up down logs bootstrap test-integration observability-up observability-config

## up: bring up the default dev stack (Postgres, PgBouncer, Redis, LocalStack [secrets+S3, buckets created], api)
up:
	$(COMPOSE) up -d --wait

## down: stop the stack (named volumes are kept; run `$(COMPOSE) down -v` to wipe data)
down:
	$(COMPOSE) down

## logs: follow logs for all services
logs:
	$(COMPOSE) logs -f

## bootstrap: (re)create the S3 buckets on a running LocalStack (normally automatic via the ready.d init hook)
bootstrap:
	$(COMPOSE) exec localstack awslocal s3 mb s3://ac-uploads || true
	$(COMPOSE) exec localstack awslocal s3 mb s3://langfuse || true

## test-integration: run the opt-in integration tests (secrets + S3) against the live LocalStack.
## Selected by the AC_RUN_LOCALSTACK env gate (NOT `-m integration`) so BOTH the secrets and S3 tests run.
test-integration:
	$(COMPOSE) up -d --wait localstack
	cd services && AC_RUN_LOCALSTACK=1 \
		AC_AWS_ENDPOINT_URL=http://localhost:4566 \
		AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test \
		$(PYTEST) -v tests/test_aws_secrets_localstack.py tests/test_s3_buckets_localstack.py

## observability-up: turn ON the PLACEHOLDER observability profile — NOT Langfuse-v3-ready (see compose comments)
observability-up:
	$(COMPOSE) --profile observability up -d

## observability-config: validate the observability profile parses (the acceptance bar for the placeholder)
observability-config:
	$(COMPOSE) --profile observability config >/dev/null && echo "observability profile: config OK"

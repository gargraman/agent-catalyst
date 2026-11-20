# Implementation Plan: Provider-key custody via a pluggable secrets backend

**Branch**: `feat/backend-m0-foundation` (feature 014) | **Date**: 2026-11-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/014-provider-key-custody/spec.md`

## Summary

Build real provider-key custody for the pilot: an administrator captures a workspace's provider API keys once (write-once), the raw value is stored only in a **pluggable secrets backend** (AWS Secrets Manager in deployed environments, LocalStack locally, an in-process store for unit tests), and the application database holds only a masked display value plus an opaque reference. At invoke and eval-run time the server resolves the workspace's active key for the agent's provider from the custody service and passes the plaintext into `RunContext.api_key` just-in-time — retiring the single shared `PROVIDER_API_KEY` env var as the production source of truth. Everything composes with the existing RBAC (`key.manage`), membership, and RLS; no raw secret ever reaches a response, log, trace, or export; and the pure `runtime/` library never imports the secrets backend (P6). The secrets seam mirrors the feature-012 `api/auth/` pattern so the cloud provider can be swapped by configuration alone.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async (asyncpg), pydantic-settings; **new:** `boto3` (AWS Secrets Manager client, under the `api` optional-deps extra, imported lazily only by the `aws` provider). Existing: litellm (transport), pyjwt (012).

**Storage**: PostgreSQL `provider_keys` table (new migration **0007**, workspace-scoped, RLS) for masked value + opaque reference + metadata. Raw secrets live only in the secrets backend (AWS Secrets Manager / LocalStack / in-process local store).

**Testing**: pytest + pytest-asyncio (`asyncio_mode=auto`); httpx ASGI client for integration; opt-in LocalStack/boto3 integration test (marker-gated, like the live-IAM test). Existing unit + integration + secret-scan CI jobs.

**Target Platform**: Linux server on AWS (deployed); local Docker compose (Postgres + Redis + LocalStack) for dev.

**Project Type**: Backend web service in the `services/` monorepo (`api`, `runtime`, `evals`).

**Performance Goals**: Negligible added latency — resolution is one indexed DB read + one secrets-backend fetch per run; no caching in v1 (correctness over micro-optimization; rotation/revoke must take effect immediately).

**Constraints**: P6 import purity (`runtime/` must not import `boto3` or `api.secrets`); no plaintext secret in any output, log, trace, or export (P5); fail-closed resolution (P3); workspace tenancy via RLS with negative cross-tenant tests (P5).

**Scale/Scope**: A handful of provider keys per workspace; one corporate pilot deployment. 4 endpoints + a pluggable secrets package + a migration + resolve-at-invoke wiring + compose/LocalStack + full Vault→AWS doc sweep.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Applies? | How this plan satisfies it |
|---|---|---|
| **P2 — Govern scope / phases** | **Yes** | M5-remainder, owner-approved in brainstorming. The Vault→AWS-Secrets-Manager switch is a **project-level reversal** and MUST get an explicit `docs/scope.md` decision-log entry (the source hierarchy requires it; a feature spec cannot silently override a design choice). Non-goals explicit in spec. |
| **P3 — Deterministic, fail-closed controls** | **Yes** | Capture/resolve/rotate/revoke are ordinary code, not model-mediated. Missing/revoked/unavailable key → fail closed with a config error before any provider call (FR-014). Deterministic multi-key resolution (FR-016). |
| **P5 — Tenant boundaries & secret protection** | **Yes (core)** | `provider_keys` is workspace-scoped with RLS (defense in depth) + server-side `key.manage` gate; raw value stored only in the secrets backend; data classification + retention in spec's Security section; **no secret in logs/traces/exports/errors** (FR-017); negative cross-tenant tests (SC-005). |
| **P6 — Reproducible runtime / purity invariant** | **Yes** | The custody service lives in `api/` (may import boto3); `runtime/` receives only the resolved plaintext string via `RunContext.api_key` — exactly the existing seam. An import-purity unit test asserts `runtime/` imports no `boto3`/`api.secrets` (FR-015). |
| **P8 — Operate; make failures visible** | **Yes** | Degradation (no key / backend down) is explicit and fail-closed, never a silent success; deny/resolve-failure logs carry no secret. |
| **P9 — Specify, test-first, evidence** | **Yes** | Full SDD artifacts; Red→Green→Refactor; named commands in quickstart; honest completion report. |
| **P7 — Provider/cost honesty** | Minimal | Resolution does not change cost computation (cost stays from our profiles). Noted, no new cost surface. |
| **P4 — Eval judge calibration** | **No** | No judge/gate change. |

**Minimum Verification Checks triggered:** *Security or secrets* (secret scan; authz/abuse; no-leak evidence), *Database or tenant behavior* (migration checks; negative cross-tenant), *API or frontend integration* (typed request/response + error contracts; drop-in to the FE `ProviderKey` shape). **Paid provider calls:** none become ordinary tests — resolution is verified with stored test values + a mocked provider gateway; the LocalStack/boto3 path is opt-in.

**Result: PASS — no violations, no Complexity Tracking required.** (Re-checked after Phase 1 design: still PASS — see bottom of this file.)

## Project Structure

### Documentation (this feature)

```text
specs/014-provider-key-custody/
├── plan.md              # This file
├── research.md          # Phase 0 — decisions & rationale
├── data-model.md        # Phase 1 — provider_keys table + SecretsProvider contract + states
├── quickstart.md        # Phase 1 — runnable validation scenarios + named commands
├── contracts/
│   ├── provider-keys-api.md      # the 4 HTTP endpoints + request/response/error schemas
│   └── secrets-provider.md       # the internal SecretsProvider interface contract
└── tasks.md             # Phase 2 (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
services/
├── api/
│   ├── secrets/                  # NEW — pluggable secrets seam (mirrors api/auth/)
│   │   ├── __init__.py
│   │   ├── base.py               # SecretsProvider Protocol + SecretsError/SecretNotFound/SecretsUnavailable
│   │   ├── registry.py           # build_secrets_provider(settings) — config-selected singleton, lazy import
│   │   ├── local_provider.py     # in-process store (unit tests + offline); non-prod PROVIDER_API_KEY seed
│   │   └── aws_provider.py       # boto3 AWS Secrets Manager (LocalStack via endpoint_url; real AWS in prod)
│   ├── provider_keys.py          # NEW — custody service: capture/rotate/revoke + resolve_provider_key(...)
│   ├── routers/
│   │   └── provider_keys.py      # NEW — POST / GET / :id/rotate / :id/revoke (key.manage)
│   ├── schemas/
│   │   └── provider_keys.py      # NEW — ProviderKeyCreate / ProviderKeyRotate / ProviderKeyOut (mirror data.ts)
│   ├── models.py                 # EDIT — add ProviderKey ORM model
│   ├── settings.py               # EDIT — AC_SECRETS_PROVIDER, AC_AWS_ENDPOINT_URL/REGION, AC_SECRETS_PREFIX; drop vault_addr
│   ├── main.py                   # EDIT — mount the provider-keys router
│   ├── routers/invoke.py         # EDIT — resolve stored key → RunContext.api_key
│   └── routers/evals.py          # EDIT — same resolve for eval-run
├── migrations/versions/
│   └── 0007_provider_keys.py     # NEW — table + enum + RLS policy + ac_app grants
└── tests/
    ├── test_secrets_provider.py          # NEW (unit) — local provider roundtrip, masking, errors
    ├── test_runtime_purity_secrets.py    # NEW (unit) — runtime/ imports no boto3/api.secrets (P6)
    ├── test_api_provider_keys.py         # NEW (integration) — capture/list/rotate/revoke, RBAC, no-plaintext
    ├── test_api_provider_key_resolve.py  # NEW (integration) — resolve-at-invoke/eval, fail-closed, cross-tenant
    └── test_aws_secrets_localstack.py    # NEW (opt-in integration) — boto3 ⇄ LocalStack roundtrip

deploy/compose.yaml               # EDIT — add localstack service (SecretsManager+KMS); api env wiring
frontend/                         # NO code change — backend matches existing ProviderKey shape (drop-in)
```

**Structure Decision**: Extend the existing `services/api` service. The secrets seam is a new `api/secrets/` package that mirrors the established `api/auth/` pattern (Protocol in `base.py`, config-selected singleton in `registry.py`, one module per provider). Custody logic (capture/rotate/revoke/resolve) lives in `api/provider_keys.py` + its router; the pure `runtime/` tree is untouched except by the import-purity test that guards it.

## Phase notes

- **Phase 0 (`research.md`)** records the resolved decisions (no open `NEEDS CLARIFICATION` — the forks were settled with the owner in brainstorming): AWS SM + pluggable seam, local-vs-aws provider split, secret naming / `vault_path` = ARN, resolve-at-invoke integration point + provider derivation, migration/RLS approach, boto3 dependency + LocalStack wiring, masking reuse, multi-key determinism, rotate/revoke semantics against SM, and the doc-sweep plan.
- **Phase 1** emits `data-model.md` (the `provider_keys` table, the `SecretsProvider` contract, status states), `contracts/` (HTTP endpoints + internal interface), and `quickstart.md` (named verification commands). `/speckit-tasks` turns these into a dependency-ordered `tasks.md`.

## Post-Design Constitution Re-Check

After Phase 1 design: **PASS.** The design keeps boto3 out of `runtime/` (guarded by test), stores no plaintext in Postgres, gates writes on `key.manage`, scopes rows by RLS with negative tests, and routes the Vault→AWS reversal through a `docs/scope.md` decision entry. No new violations; no Complexity Tracking entries needed.

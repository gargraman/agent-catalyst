---
description: "Task list for feature 014 — provider-key custody via a pluggable secrets backend"
---

# Tasks: Provider-key custody via a pluggable secrets backend

**Input**: Design documents from `specs/014-provider-key-custody/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/
**Tests**: INCLUDED — the constitution (P9) mandates Red→Green→Refactor and the spec's acceptance names tests. Each behavior-changing task pairs with a failing test first.

## Format: `[ID] [P?] [Story] Description`
- **[P]**: can run in parallel (different files, no incomplete-task dependency)
- **[Story]**: US1 capture+list · US2 resolve-at-invoke · US3 rotate · US4 revoke
- All paths are repo-relative; backend work runs from `services/`.

**Conventions (from reverify):** reuse the RLS helper form in `migrations/versions/0005_*` (`workspace_id = NULLIF(current_setting('app.workspace_id', true),'')::uuid`); extend the existing `tests/test_runtime_purity.py` pattern for P6; the resolve seam is the `RunContext(api_key=…)` call at `routers/invoke.py:95` + `routers/evals.py:85` (replacing `PROVIDER_API_KEY` from `deps.py:31`). Test command: `cd services && .venv/bin/python -m pytest -p no:cacheprovider -q <path>`.

---

## Phase 1: Setup (Shared Infrastructure)

- [X] T001 Add `boto3` to the `api` optional-deps extra in `services/pyproject.toml` and install it into `services/.venv` (`.venv/bin/pip install -e '.[api,test]'`). Scaffolding only — no behavior.
- [X] T002 [P] Add secrets settings to `services/api/settings.py`: `secrets_provider` (`AC_SECRETS_PROVIDER`, default `local`), `aws_endpoint_url` (`AC_AWS_ENDPOINT_URL`, default `None`), `aws_region` (`AC_AWS_REGION`, default `us-east-1`), `secrets_prefix` (`AC_SECRETS_PREFIX`, default `ac`); REMOVE the now-dead `vault_addr`/`AC_VAULT_ADDR` slot. Update `services/tests/test_settings.py` if it references `vault_addr`.
- [X] T003 [P] Add a `localstack` service (Secrets Manager + KMS) to `deploy/compose.yaml` and wire the `api` + `migrate` containers with `AC_SECRETS_PROVIDER=aws`, `AC_AWS_ENDPOINT_URL=http://localstack:4566`, `AC_AWS_REGION`, and dummy static AWS creds (dev-only).

**Checkpoint**: dependency, config, and local emulator available.

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Blocks ALL user stories.** The secrets seam + the `provider_keys` table + schemas + masking must exist before any story.

- [X] T004 [P] RED: unit tests in `services/tests/test_secrets_provider.py` — `LocalProvider` put→get→rotate→disable roundtrip; `get` on a missing/disabled ref raises `SecretNotFound`; `build_secrets_provider()` selects `local` by config; masking helper (6-head/4-tail, full-bullets short) mirrors `frontend/src/lib/secrets.ts`. Run; confirm it FAILS (no module yet).
- [X] T005 [P] GUARD: extend `services/tests/test_runtime_purity.py` (or add `services/tests/test_runtime_purity_secrets.py`) asserting no module under `runtime/` imports `boto3` or `api.secrets` (P6, FR-015). This is a passing invariant guard, not behavior-Red.
- [X] T006 GREEN: create `services/api/secrets/base.py` — `SecretsProvider` Protocol (async `put`/`get`/`rotate`/`disable`, `id`) + `SecretsError`/`SecretNotFound`/`SecretsUnavailable`.
- [X] T007 GREEN: create `services/api/secrets/local_provider.py` (in-process store) + `services/api/secrets/registry.py` (`build_secrets_provider()` cached singleton, lazy-imports aws) + `services/api/secrets/__init__.py`; make T004 pass.
- [X] T008 GREEN: create `services/api/secrets/aws_provider.py` — boto3 Secrets Manager (`create_secret`/`get_secret_value`/`put_secret_value`/`delete_secret`, `endpoint_url` from settings, naming `{prefix}/{env}/{ws}/provider-key/{id}`, blocking calls off the event loop); boto3 imported at module top (module itself lazy-loaded by the registry). Keep T005 green.
- [X] T009 [P] RED: integration test in `services/tests/test_api_provider_keys.py` for the table + tenancy — seeding a `provider_keys` row and reading it back under RLS works, and a row in workspace B is invisible to workspace A. Run; confirm it FAILS (no table/model).
- [X] T010 GREEN: add the `ProviderKey` ORM model + `provider_key_status` enum binding to `services/api/models.py` (WorkspaceScoped; cols per data-model.md; `UNIQUE(workspace_id, label)`).
- [X] T011 GREEN: write migration `services/migrations/versions/0007_provider_keys.py` — create enum + table + `(workspace_id, provider, status)` resolution index, enable/force RLS and the `_ws_isolation` policy + grants reusing the 0005 helper pattern; downgrade drops table + enum. Run `alembic upgrade head`; make T009 pass.
- [X] T012 [P] GREEN: create `services/api/schemas/provider_keys.py` — `ProviderKeyCreate`, `ProviderKeyRotate`, `ProviderKeyOut` (serializes to the FE `ProviderKey` shape: `added`←`created_at`, `rotatedAt`←`rotated_at`; NO `value`, NO `vault_path`).
- [X] T013 [P] GREEN: create `services/api/provider_keys.py` (custody-service module) with the pure `mask_secret()` helper + a `_provider_key_out()` projection; make the masking assertions in T004 pass.

**Checkpoint**: secrets seam + storage + contracts ready; stories can proceed.

---

## Phase 3: User Story 1 — Capture & list provider keys (Priority: P1) 🎯 MVP

**Goal**: An admin captures a key once (write-once) and sees only a masked roster. **Independent test**: capture as admin → `201` masked-only (no raw value); list masked-only; non-admin denied; duplicate label conflicts; another workspace's key absent.

- [X] T014 [US1] RED: extend `services/tests/test_api_provider_keys.py` — `POST /api/v1/provider-keys` returns `201` with masked + status, NEVER the raw `value` (and the stored row holds no plaintext); `GET` returns masked only; non-`key.manage` caller → `403`; duplicate `(workspace,label)` → `409`; cross-tenant list omits workspace B's key. Run; confirm FAILS.
- [X] T015 [US1] GREEN: implement `capture_provider_key(session, ws, body)` + `list_provider_keys(session, ws)` in `services/api/provider_keys.py` — validate non-empty; `put` raw value to `build_secrets_provider()`; compute `masked`; insert row; atomic (no row if backend write fails → `config_error`).
- [X] T016 [US1] GREEN: create `services/api/routers/provider_keys.py` — `POST /api/v1/provider-keys` + `GET /api/v1/provider-keys`, both `dependencies=[Depends(require_permission("key.manage"))]`, workspace-scoped via `resolve_workspace`/`tenant_db`; map duplicate → `ConflictStale`/`409`.
- [X] T017 [US1] GREEN: mount the provider-keys router in `services/api/main.py`. Make T014 pass; run full file.

**Checkpoint**: keys can be captured + listed, masked-only, RBAC + tenant safe.

---

## Phase 4: User Story 2 — Resolve the stored key at invoke & eval (Priority: P1)

**Goal**: invoke + eval-run draw the provider credential from the stored key, fail closed when absent. **Independent test**: seed an active key → invoke uses it; remove it → `config_error` with zero provider calls; cross-tenant impossible; non-prod env fallback.

- [X] T018 [US2] RED: integration test `services/tests/test_api_provider_key_resolve.py` — with an active key stored for the agent's provider, invoke AND eval-run resolve/use the STORED key (assert via mocked gateway capturing the key); with NO active key, both fail closed with `config_error` and the mocked gateway is NEVER called; cross-tenant resolve impossible (RLS); `AC_ENV=dev` falls back to `PROVIDER_API_KEY`, `AC_ENV=prod` does not. Run; confirm FAILS.
- [X] T019 [US2] GREEN: implement `async def resolve_provider_key(session, ws, provider) -> str` in `services/api/provider_keys.py` — select most-recently-added `active` key for `(ws, provider)` (FR-016 order), `get()` its `vault_path`; map `SecretNotFound`/`SecretsUnavailable`/no-row → a `config_error`; non-prod (`AC_ENV != prod`) fallback to `PROVIDER_API_KEY`.
- [X] T020 [US2] GREEN: edit `services/api/routers/invoke.py` — after `version.to_snapshot()`, derive the provider from the snapshot (confirm field: `profile_snapshot`/`model_config`) and `api_key = await resolve_provider_key(session, ws, provider)`; pass it into `RunContext(api_key=…)` (replacing `PROVIDER_API_KEY`); a resolve failure surfaces as the pre-call `config_error` path (no stream/provider call).
- [X] T021 [US2] GREEN: edit `services/api/routers/evals.py` — same resolve + `RunContext(api_key=…)` change for `create_eval_run`. Make T018 pass.

**Checkpoint**: the shared env var is retired as the prod source of truth; runs fail closed without a key.

---

## Phase 5: User Story 3 — Rotate a provider key (Priority: P2)

**Goal**: admin rotates; new secret used, old stops; identity preserved. **Independent test**: rotate → next run uses new secret, old no longer resolves, `rotatedAt` updates.

- [X] T022 [US3] RED: extend `services/tests/test_api_provider_keys.py` — `POST /api/v1/provider-keys/{id}/rotate` replaces the secret, updates `masked` + `rotatedAt`, keeps id/label/status; a resolve afterward yields the NEW value and the old no longer resolves; non-admin `403`; cross-tenant `404`. Run; confirm FAILS.
- [X] T023 [US3] GREEN: implement `rotate_provider_key(session, ws, id, body)` in `services/api/provider_keys.py` (backend `rotate`; update masked + `rotated_at`) + the `POST …/{id}/rotate` route in `services/api/routers/provider_keys.py` (`key.manage`; `404` on unknown/cross-tenant). Make T022 pass.

**Checkpoint**: rotation works end-to-end.

---

## Phase 6: User Story 4 — Revoke a provider key (Priority: P2)

**Goal**: admin revokes; tombstone retained, resolution fails closed. **Independent test**: revoke → row retained as `revoked`, backend secret disabled, resolution fails closed, idempotent.

- [X] T024 [US4] RED: extend `services/tests/test_api_provider_keys.py` — `POST /api/v1/provider-keys/{id}/revoke` sets `status=revoked`, RETAINS the row (no delete), disables the backend secret so resolve fails closed; idempotent on an already-revoked key; non-admin `403`; cross-tenant `404`. Run; confirm FAILS.
- [X] T025 [US4] GREEN: implement `revoke_provider_key(session, ws, id)` in `services/api/provider_keys.py` (status→`revoked` via UPDATE, never DELETE; backend `disable`) + the `POST …/{id}/revoke` route. Make T024 pass.

**Checkpoint**: all four stories independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T026 [P] Opt-in integration test `services/tests/test_aws_secrets_localstack.py` — `AwsProvider` put→get→rotate→disable roundtrip against LocalStack, gated by an opt-in env flag (`AC_RUN_LOCALSTACK=1`) like the live-IAM test (FR-019, SC-006). Written + verified it skips cleanly by default (`1 skipped`). **Live LocalStack run EXECUTED and PASSED** (`AC_RUN_LOCALSTACK=1` against `localstack/localstack:3` secretsmanager on :4566 → `1 passed`): the `aws` `AwsProvider` put→get→rotate→disable roundtrip works against emulated Secrets Manager (SC-006).
- [X] T027 Run the full regression `cd services && .venv/bin/python -m pytest -p no:cacheprovider -q` — all green, no regression against feature 013's suite; retain the count.
- [X] T028 Secret-scan the diff (CI `secret-scan` job / local scan) — confirm no raw key, no `vault_path`, in code/tests/fixtures (SC-001).
- [X] T029 [P] Docs: add the `docs/scope.md` decision-log entry — HashiCorp Vault Transit → pluggable secrets / AWS Secrets Manager (rationale: AWS deployment target; kept swappable). Required by the Governance source hierarchy (P2).
- [X] T030 [P] Docs: Vault→AWS sweep across the LOAD-BEARING PROSE docs — `backend/00-overview` (stack list, settings sketch, invoke flow), `backend/01-data-model §4.3` (as-built DDL + enum + UNIQUE + RLS note), `backend/09-security-tenancy §9` (as-built callout + P6 path correction + deferred-list), `backend/11-deployment` (as-built callout + runnable compose snippet: localstack not vault), `backend/02-provider-gateway`, `backend/03-runtime` (comment), `backend/08-api-surface`, `backend/data-isolation-topology`, `backend/README`, `backend-integration-status` (provider-keys row → BUILT, totals, seam count 253→257), `backend-integration-map`, `backend-integration-plan`, `spec.md` (Secrets row + B12), `frontend-auth-access-plan`. **DEFERRED to a separate regeneration pass:** ~45 GENERATED diagram artifacts (`docs/diagrams/backend-design/` ×27, `frontend-flows/` ×14, `backend-services/` ×4) — hand-editing generated HTML/JSON is error-prone; regenerate via the archify/diagram-design sources. Remaining single `vaulted`/`vault_path` mentions are for the UNBUILT cred families (tools/MCP/channels/sources) and resolve via the corrected `09 §9`. Docs-only (P9).
- [X] T031 Ran `specs/014-provider-key-custody/quickstart.md` scenarios end-to-end. Evidence: `alembic current` = `0007 (head)`; feature-014 suites (secrets_provider + runtime_purity + api_provider_keys + api_provider_key_resolve + aws_secrets_localstack) = **30 passed, 1 skipped** (opt-in LocalStack); full regression `pytest -q` = **562 passed, 1 skipped**, no regression vs 013. Fixed a stale quickstart ref (`test_runtime_purity_secrets.py` → `test_runtime_purity.py`). Scenarios 1–7 covered by the passing API/resolve suites; scenario 8 (backend swap, SC-006) verified LIVE — opt-in `aws` test passed against LocalStack secretsmanager (`1 passed`).

---

## Dependencies & Execution Order

- **Setup (T001–T003)** → first. T001 blocks T008 (boto3). T002/T003 [P].
- **Foundational (T004–T013)** → blocks all stories. RED (T004, T009) before their GREEN; T006→T007 (pass T004); T010→T011 (pass T009); T012/T013 [P]; T005 guard anytime.
- **US1 (T014–T017)**, **US2 (T018–T021)**, **US3 (T022–T023)**, **US4 (T024–T025)** → each after Foundational. Within each: RED before GREEN. US2 is independently testable by seeding a `provider_keys` row (does not require US1 routes), but shares `api/provider_keys.py` with US1/US3/US4 (serialize edits to that file).
- **Polish (T026–T031)** → after the stories it covers; T029/T030 docs [P]; T027 after all code tasks.

## Parallel Opportunities
- Setup: T002 ∥ T003.
- Foundational: T004 ∥ T005 ∥ T009 (tests, different files); T012 ∥ T013.
- Docs: T029 ∥ T030.
- Across stories: with multiple implementers, US1/US3/US4 touch `provider_keys.py`+router (serialize), while US2 adds `resolve_provider_key` + edits `invoke.py`/`evals.py` (mostly separate).

## Implementation Strategy
- **MVP = Setup + Foundational + US1 + US2** (P1s): keys can be captured, listed, and actually used at invoke/eval, fail-closed. Stop and validate here.
- Then US3 (rotate) and US4 (revoke) as P2 increments.
- Docs sweep + opt-in LocalStack + full regression close the DoD.

## Notes
- Verify every RED test FAILS for the intended behavior (not an import/env error) and show it before writing GREEN (P9).
- No raw secret or `vault_path` in any response, log, trace, export, or test fixture (FR-017, SC-001).
- `runtime/` stays boto3-free (T005 guards it).
- No live paid provider call becomes an ordinary test; resolution uses stored test values + a mocked gateway; LocalStack path is opt-in.

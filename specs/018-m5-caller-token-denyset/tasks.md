---
description: "Task list — 018 M5 Caller-Token Plane + Immediate Revocation (Deny-Set)"
---

# Tasks: M5 — Caller-Token Plane + Deny-Set

**Input**: Design documents from `/specs/018-m5-caller-token-denyset/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/caller-token.md · **Depends on 016** (shared Redis client, already built)
**Tests**: REQUIRED — Constitution P9 mandates test-first (Red→Green→Refactor). All paths under `services/`.

**Single user story (US1).** MVP = US1 = the whole feature (mint → revoke → immediate deny-set revocation).
**Owner rulings (2026-11-12):** D2 accepted (deny-set fail-closed → Redis soft-SPOF for console, validated in 021); D5 reuse (`agent.publish` for mint/revoke, `member.manage` for admin revoke-user) — **no** new `token.manage`. D1/D3/D4 and 017's 502 stand.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no incomplete deps)
- Paths are repo-relative; run commands from `services/`.

---

## Phase 1: Setup

- [ ] T001 [P] Add deny-set settings to `services/api/settings.py`: `AC_DENYSET_TTL_S` (default 3600) and `AC_DENYSET_STREAM_RECHECK_S` (default 2). (`AC_REDIS_URL` + `redis`/`fakeredis` already present from 016 — do not re-add.)

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Must complete before US1 can go Green. Scaffolding + schema only.**

- [ ] T002 Create `services/migrations/versions/0008_api_tokens.py` (down_revision `0007`): `api_tokens` per data-model.md — `workspace_id`/`agent_id` FKs, `token_hash` UNIQUE, `prefix`, `scopes` jsonb, timestamps, `expires_at?`/`revoked_at?`; WorkspaceScoped RLS mirroring `0006`/`0007`; CHECK `expires_at IS NULL OR expires_at > created_at`.
- [ ] T003 Add the `ApiToken` ORM model to `services/api/models.py` (match the `api_tokens` columns; WorkspaceScoped base like the other tenant tables).
- [ ] T004 [P] Create `services/api/auth/denyset.py` **stub**: `async def is_denied(subject, token_id) -> bool` and `async def deny(*, subject=None, token_id=None, ttl_s)` signatures; docstring states the fail-**closed** policy (inverse of `ratelimit.check`). Scaffold only (raise `NotImplementedError` in bodies).
- [ ] T005 [P] Create `services/api/publish/__init__.py` + `services/api/publish/token_auth.py` **stub**: `CallerCtx` dataclass (`workspace_id`, `token_id`, `subject`, `role="executer"`, `teams`) and `async def resolve_caller(request, session) -> CallerCtx` scaffold.
- [ ] T006 [P] Add `EgressBlocked`-style parity NOT needed here; instead confirm `AuthRequired`/`access.denied`/`RateLimited` envelopes suffice for the caller plane (no new error class expected — 401 uses `auth.unauthorized`; verify that code exists or add it to `errors.py`).

**Checkpoint**: migration applies; `ApiToken` imports; deny-set + token_auth modules import cleanly.

---

## Phase 3: User Story 1 — Caller tokens + immediate revocation (P1) 🎯 MVP

**Goal**: Publishers mint revocable caller tokens (secret shown once, hash+prefix persisted); a token/subject revocation is rejected on the very next request (both console session AND caller token), with an in-flight stream cut; deny-set fails closed on Redis down.

**Independent Test**: Mint (capture one-time secret) → invoke 200 → revoke → next invoke 401; admin revoke-user → active session + live token both 401 next request + in-flight stream cut; Redis down → 401.

### Tests for User Story 1 (write FIRST, confirm they FAIL on behavior) ⚠️

- [ ] T007 [US1] Add `fakeredis` async fixture + tenant-session scaffold in `services/tests/test_caller_token_denyset.py` (import real app/deps; no behavior assertions) so Red tests fail on behavior, not import.
- [ ] T008 [US1] Red `test_mint_shows_secret_once_persists_hash`: `POST /api/v1/agents/{id}/tokens` returns full secret once; `GET` lists prefix only; DB row holds `token_hash`+`prefix`, not the secret (FR-010, AS1).
- [ ] T009 [US1] Red `test_secret_never_logged_or_reechoed`: capture logs across mint+list+invoke; assert the raw secret never appears in logs/trace/any later response (FR-011, SC-005).
- [ ] T010 [US1] Red `test_revoke_rejects_next_invoke`: valid token → published invoke 200; `DELETE /api/v1/tokens/{id}` → next invoke 401 (FR-012, AS2).
- [ ] T011 [US1] Red `test_inflight_stream_cut_on_revoke`: start a streaming invoke, revoke mid-stream; stream emits an `error` frame (`auth.unauthorized`) and stops within `AC_DENYSET_STREAM_RECHECK_S` (FR-012, D3).
- [ ] T012 [US1] Red `test_revoke_user_cuts_session_and_token`: `POST /api/v1/org/revoke-user {subject}` → the subject's **console session** AND a **live caller token** both 401 on next request (FR-013, AS3).
- [ ] T013 [US1] Red `test_denyset_redis_down_fails_closed`: with `AC_REDIS_URL` set but Redis raising, an auth decision → 401 (fail closed), not allowed (FR-024b, AS4). Also assert `AC_REDIS_URL` **unset** → no-op allow (dev parity).
- [ ] T014 [US1] Red `test_denyset_ttl_at_least_one_hour`: a deny entry's TTL ≥ 3600 (FR-014).
- [ ] T015 [US1] Red `test_expired_token_rejected`: a token past `expires_at` → 401 (FR-015, AS5).
- [ ] T016 [US1] Red `test_rbac_on_token_and_admin_routes`: builder cannot mint/revoke (403); non-admin cannot `revoke-user` (403); publisher can mint/revoke; admin can revoke-user (FR-016, D5).
- [ ] T017 [US1] Red `test_published_and_console_share_code_path`: the `/v1` (token) and `/api/v1` (session) invoke routes produce identical frames for the same snapshot (D1 regression).
- [ ] T018 [US1] Red `test_api_tokens_rls_cross_tenant_denied`: a token minted in workspace A is not readable/usable from workspace B (RLS; mirror `test_integration_rls` style where feasible, else a tenant-session unit test).

### Implementation for US1 (make the above Green, dependency order)

- [ ] T019 [US1] Implement `services/api/auth/denyset.py`: `is_denied` = pipelined `EXISTS deny:sub`, `EXISTS deny:tok`; `deny` = `SET … EX ttl_s`. `AC_REDIS_URL` unset → allow; configured-but-error → **raise** (caller converts to 401). Depends on 016 `redis_client`.
- [ ] T020 [US1] Implement `resolve_caller` in `services/api/publish/token_auth.py`: parse bearer → `sha256` lookup in `api_tokens`; unknown/`revoked_at` → 401; `expires_at` past → 401; deny-set check (`is_denied` raise → 401); per-token rate limit via `ratelimit.check("rl:token:{id}", …)` → 429; build `CallerCtx`; best-effort `last_used_at` touch. Depends on T003, T019.
- [ ] T021 [US1] Wire the deny-set into `services/api/deps.py` `session_principal`: after `provider.verify()`, call `denyset.is_denied(subject, None)`; denied → 401; `is_denied` raising (Redis down) → 401 (fail closed, D2). Depends on T019.
- [ ] T022 [US1] Add `require_caller` dependency in `deps.py` wrapping `resolve_caller`; adapt `CallerCtx` → the `PolicyPrincipal` the access-policy spine (`ensure_use`) consumes so published invoke still honors access policy (D4). Depends on T020.
- [ ] T023 [US1] Create `services/api/routers/tokens.py`: `POST/GET /api/v1/agents/{id}/tokens`, `DELETE /api/v1/tokens/{id}` → `require_permission("agent.publish")`; `POST /api/v1/org/revoke-user` → `require_permission("member.manage")` writes deny-set (`deny` with `AC_DENYSET_TTL_S`) + `security.revoke_user` audit row. Register in `main.py`. Depends on T003, T019.
- [ ] T024 [US1] Refactor `services/api/routers/invoke.py`: extract the handler body into a shared `_run_invoke(...)`; add the published `POST /v1/agents/{id}/invoke` route (prefix `/v1`, `require_caller`, `source="published"`) calling the SAME path as console (D1). Register the `/v1` router in `main.py`. Depends on T022.
- [ ] T025 [US1] Thread the in-flight deny-set re-check into `_event_stream` (D3): between frames, every `AC_DENYSET_STREAM_RECHECK_S`, re-check `is_denied(subject, token_id)`; on hit emit `sse.error_frame("auth.unauthorized", …)` and stop. Depends on T024, T019.
- [ ] T026 [US1] Re-key the published invoke rate limit per-token (`rl:token:{id}`) while console invoke stays per-principal (reuses 016's engine; the 016 TODO at `deps.py:181`). Depends on T020.

### Refactor (keep all US1 tests green)

- [ ] T027 [US1] Extract token hashing/prefix + the caller→policy-principal adapter into small pure helpers; confirm no secret in any log/key; confirm `services/runtime/` untouched (P6).

**Checkpoint**: `pytest tests/test_caller_token_denyset.py tests/test_migration_0008.py` green.

---

## Phase 4: Polish & Cross-Cutting

- [ ] T028 Add `services/tests/test_migration_0008.py` (mirror `test_migration_0006`): upgrade/downgrade + RLS policy present.
- [ ] T029 Run full regression `pytest -q` — the 8 `session_principal` callers stay green after the deny-set check (D2).
- [ ] T030 [P] `ruff check . && mypy .` clean (deferred to CI if tools absent locally — note it honestly).
- [ ] T031 [P] Secret-scan: assert no token secret appears in logs, deny-set/RL keys, or any envelope (P5/FR-011/SC-005).
- [ ] T032 Run `quickstart.md` validation.
- [ ] T033 At DoD: update tracker memory `m5-security-split` (018 ✅) and reconcile the caller-token / deny-set rows in `docs/backend-integration-status.md` + `docs/implementation-plan.md` (per update-docs-after-feature).

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002–T006)** → **US1 (T007–T027)** → **Polish (T028–T033)**.
- Within US1: **tests (T007–T018) before implementation (T019–T026)**; T019 → T020 → {T021, T022} → T023 → T024 → T025; T026 after T020; refactor T027 last.
- T002/T003 (schema+ORM) block T020/T023. T019 (deny-set) blocks T020/T021/T023/T025.

### Parallel Opportunities

- T004 ∥ T005 ∥ T006 (different files) after T002/T003 started.
- Red tests T008–T018 share one file → write sequentially after the T007 scaffold.
- T030 ∥ T031 in Polish.

---

## Implementation Strategy

Single-story MVP, strictly test-first (Red shown before Green, P9). Deny-set fails **closed** everywhere (D2) — the opposite of 016's limiter; the separate module guards against copying the wrong error policy. Published invoke reuses `runtime.invoke` (D1, P6). `fakeredis` + error injection + the tenant test session cover all paths — no live provider/Redis/Postgres calls.

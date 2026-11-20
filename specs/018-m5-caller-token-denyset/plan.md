# Implementation Plan: M5 — Caller-Token Plane + Immediate Revocation (Deny-Set)

**Branch**: `feat/backend-m0-foundation` (feature dir `018-m5-caller-token-denyset`) | **Date**: 2026-11-12 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/018-m5-caller-token-denyset/spec.md` · **Parent umbrella**: [`015-m5-security-completion`](../015-m5-security-completion/) (shared design) · **Depends on**: 016 (shared Redis client) · **Blocks**: 019 (OIDC reuses the deny-set check)

## Summary

Stand up the **published-invoke auth plane** (caller bearer tokens, role `executer`) and the **immediate-revocation deny-set** that fails closed. Three load-bearing pieces: (1) `api_tokens` persistence + mint/list/revoke routes where only a `sha256` hash + display prefix are stored and the secret is shown exactly once; (2) `resolve_caller` — the per-invoke auth dependency that hash-looks-up the token, rejects revoked/expired, checks the deny-set, rate-limits (reusing 016's engine, now keyed per-token), and builds a `CallerCtx`; (3) a Redis **deny-set** (`deny:sub:{subject}` / `deny:tok:{id}`, TTL ≥ 1h) wired into **both** the caller path **and** the existing console `session_principal`, plus an admin `revoke-user`/`force-logout` route — so a leaked token or offboarded user is cut on their *next* request and any **in-flight stream is severed**.

**Key reuse / deltas found in code (grounding the shared 015 design against what's actually on disk):**
- **Migration number is `0008`, not `0007`.** The 015 data-model says "create `0007_api_tokens`", but `0007_provider_keys.py` already took that slot (feature 014). `api_tokens` is **not** in any existing migration → new `0008_api_tokens.py`.
- **016 left the exact seam we need.** `ratelimit.check(key, limit, window_s)` (`ratelimit.py:58`) is identity-agnostic, and `rate_limit(route_class)` (`deps.py:177`) already keys invoke per-*principal* with a TODO to re-key per-token in 018. `AppError.headers` (`errors.py:20`) already exists, so a 429 from the caller path carries headers for free.
- **Invoke is already "one code path".** The current `/api/v1/agents/:id/invoke` (`invoke.py:61`) is the **console/playground** plane (`source="playground"`, `session_principal`). Per `08 §244-250` playground and published invoke **share one body + one `runtime.invoke` call**, differing only in auth dep + `run_source`. 018 adds the **published** plane as a second route over a refactored shared handler — it does **not** fork the runtime.
- **No `token.manage` permission exists.** RBAC roles are `viewer < builder < publisher < admin`; `agent.publish` is held by exactly publisher+admin, and `member.manage` is admin-only. We **reuse** these rather than mint a new permission (which would force a cross-repo `permissions.ts` change + RBAC-drift-test regen). See Decision D5.
- Frontend already has the mock contracts (`useTokens`/`addToken`/`revokeToken`, `org/revoke-user`) — shapes are drop-in per the `@backend` markers (`08 §173-175, §220`).

## Technical Context

**Language/Version**: Python 3.12 (`services/`)
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2; `redis` (async) + `fakeredis` (test) — both already added in 016. `hashlib.sha256` + `secrets` (stdlib) for token mint/hash.
**Storage**: Postgres (`api_tokens`, RLS/WorkspaceScoped) — **new** `0008` migration; Redis (deny-set, transient) — reuses 016's client.
**Testing**: pytest async + `fakeredis`; deterministic security TDD only (no model/prompt behavior → no eval layer, per 015 plan).
**Project Type**: Web service (`services/api`)
**Performance Goals**: `resolve_caller` adds one indexed Postgres lookup + one pipelined Redis round-trip per invoke; the in-flight stream re-check is **throttled** (see D3) so a token-delta stream isn't one Redis call per frame.
**Constraints**: deny-set + token auth **fail closed** on Redis/DB error (FR-024b) — the one deviation from 016's fail-open; secret/hash never logged/exported (FR-011, P5); deny-set TTL ≥ max token/session TTL (≥ 1h); `services/runtime/` untouched (P6).
**Scale/Scope**: internal pilot; the published-invoke plane + token CRUD (3 routes) + admin revoke (1 route) + deny-set wired into 2 auth paths.

## Decisions requiring review (the load-bearing choices)

> These are the calls where the spec/shared-design leave room, or where fidelity to it has a real cost. Flagged explicitly for critical review before tasks/code.

**D1 — Published-invoke route: second route over a refactored shared handler.**
Add `POST /v1/agents/{id}/invoke` (prefix `/v1`, **not** `/api/v1`) authed by `resolve_caller`, `source="published"`. Refactor the current `invoke.py` handler body into a shared `_run_invoke(snapshot, inputs, ctx, deny_check)` so playground (`/api/v1`, session) and published (`/v1`, token) call the **same** code (08's "one code path" invariant). *Alternative rejected:* a single route that sniffs session-vs-token auth — muddies the two planes' URL prefixes (`08 §41-46`) and their RBAC, and makes the OpenAPI-per-agent narrowing (`08 §691`) harder.

**D2 — Deny-set is checked on BOTH planes, and that makes Redis a soft-SPOF for the console.**
FR-013 says an admin revoke must reject *active console sessions* too — so `session_principal` (used by **every** console route) must gain a deny-set check after `provider.verify()`. FR-024b says the deny-set **fails closed**. Net consequence: **a Redis outage locks out the entire console**, not just invoke. This is the spec's intent (security control > availability for revocation), and it's exactly what **021** (Redis-SPOF degraded-mode) exists to validate/document. *Surfacing it because it's the biggest blast-radius decision here.* Mitigation inside 018: the deny-set check is a single `EXISTS` on two keys (pipelined); if `AC_REDIS_URL` is **unset** (dev/test) the check no-op-allows (parity with 016), so only a *configured-but-down* Redis trips the fail-closed path.

**D3 — In-flight stream cut: re-check the deny-set on a time interval, not per frame.**
FR-012 requires a revoked mid-stream invoke to be **cut**. Checking the deny-set on every SSE frame is one Redis round-trip per token-delta (far too chatty). Plan: the `_event_stream` generator re-checks `deny:sub`/`deny:tok` **every N seconds** (proposed `AC_DENYSET_STREAM_RECHECK_S`, default 2s) between frames; on a hit it emits an `error` frame (`auth.unauthorized`) and stops. Worst-case leak window = the interval. *Alternative rejected:* per-frame check (perf) and no-check-until-next-request (violates "in-flight cut").

**D4 — Deny-set keyed by subject AND token-id; `resolve_caller` returns a `CallerCtx` that satisfies the access-policy spine.**
`resolve_caller` builds a caller principal with `workspace_id` pinned from the token row, `role="executer"`, `teams=scopes.teams`. It must be adaptable to the `PolicyPrincipal` the access-policy spine (`ensure_use`, feature 013) consumes, so published invoke still honors per-resource access policy. `executer` is not in the RBAC map (it's a plane role, not a console role) — invoke authz is the access-policy gate, not `has_permission`. *Confirm this is the intended model (it matches `08 §54` "caller token carries `scopes.teams`").*

**D5 — Reuse existing permissions rather than mint `token.manage`.**
Mint/list/revoke tokens → `require_permission("agent.publish")` (publisher+, matches "publisher+" in the contract). Admin `revoke-user`/`force-logout` → `require_permission("member.manage")` (admin-only). *Alternative rejected:* a new `token.manage` permission — cleaner semantically but forces a `frontend/src/lib/permissions.ts` edit + `gen_rbac.py` regen + the RBAC-drift test, i.e. drags the frontend into a backend security slice. If you'd rather have the dedicated permission, that's a one-line scope add.

## Constitution Check

*GATE: pre-Phase 0 + re-checked post-Phase 1.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P1 outcome | ✅ | Publisher mints revocable caller tokens; admin cuts access immediately. Measurable SC-004/005. No LLM. |
| P2 scope/phases | ✅ | Bounded slice of the owner-approved M5 split; tool-approval still deferred (untouched). |
| **P3 deterministic fail-closed** | ✅ | Token auth + deny-set are server-side, deterministic, and **fail closed** on Redis/DB error (FR-024b) — no deviation here (unlike 016's rate-limit fail-open). |
| P4 evidence | ✅ (N/A judge) | No model/prompt behavior; deterministic TDD. |
| **P5 data/tenant** | ✅ | Only `sha256(secret)`+prefix persisted; raw secret shown once, never logged/traced/exported (SC-005, secret-scan test); `api_tokens` is WorkspaceScoped under RLS; deny-set keys use subject/token-id, never the secret. |
| P6 reproducible | ✅ | `services/runtime/` untouched; published invoke calls the SAME `runtime.invoke` (D1). No snapshot change. |
| P7 providers/cost | ✅ | No provider/cost contract change; per-token rate limit protects spend. |
| P8 operate/visible | ✅ | Revocation is explicit (401 + audit `security.revoke_user`); deny-set-down → fail-closed is visible as 401s; degraded behavior validated in 021. |
| P9 test-first | ✅ | Red→Green→Refactor; `pytest tests/test_caller_token_denyset.py`; no live calls. |

**Gate: PASS** (no new deviation; deny-set fail-closed is the P3 default).

## Design

### Components

- **`migrations/versions/0008_api_tokens.py`** (NEW): `api_tokens` per 015 data-model (`workspace_id` FK, `agent_id` FK, `name`, `token_hash` UNIQUE, `prefix`, `scopes` jsonb, `created_at`, `last_used_at?`, `expires_at?`, `revoked_at?`). WorkspaceScoped RLS policy mirroring 0006/0007 patterns. CHECK `expires_at > created_at` when set.
- **`api/models.py`** CHANGE: add the `ApiToken` ORM model.
- **`api/auth/denyset.py`** (NEW): `async def is_denied(subject, token_id) -> bool` (pipelined `EXISTS deny:sub:{subject}`, `EXISTS deny:tok:{id}`); `async def deny(subject?, token_id?, ttl_s)`. **Redis unset → allow (no-op parity); Redis configured-but-error → raise → caller fails closed.** Distinct from `ratelimit.check` precisely because the error policy inverts (closed, not open).
- **`api/publish/token_auth.py`** (NEW): `resolve_caller(request, session) -> CallerCtx` — the §"Caller auth resolution" order from contract caller-token.md: (1) `sha256(bearer)` lookup in `api_tokens`, unknown/`revoked_at` → 401; (2) `expires_at` past → 401; (3) deny-set → 401 (Redis down → 401); (4) per-token rate limit via 016's `ratelimit.check("rl:token:{id}", …)` → 429; (5) build `CallerCtx(workspace_id, token_id, subject, role="executer", teams)`. Best-effort `last_used_at` touch.
- **`api/deps.py`** CHANGE: (a) `session_principal` gains a deny-set check after `provider.verify()` (D2) — reject denied subject with 401 before any handler runs; (b) a `require_caller` dependency wrapping `resolve_caller` for the published route; (c) the invoke rate-limit on the published plane keys per-token (`rl:token:{id}`) while console stays per-principal.
- **`api/routers/tokens.py`** (NEW): `POST/GET /api/v1/agents/{id}/tokens` (mint shows secret once / list masked), `DELETE /api/v1/tokens/{id}` (sets `revoked_at`) — all `require_permission("agent.publish")`; `POST /api/v1/org/revoke-user` (`require_permission("member.manage")`) writes the deny-set + audit row.
- **`api/routers/invoke.py`** CHANGE: refactor the handler body into a shared `_run_invoke(...)`; add the `/v1/agents/{id}/invoke` published route (token auth, `source="published"`); thread a periodic deny-set re-check into `_event_stream` (D3) so an in-flight stream is cut.
- **`api/settings.py`** CHANGE: `AC_DENYSET_TTL_S` (default 3600), `AC_DENYSET_STREAM_RECHECK_S` (default 2). (`AC_REDIS_URL` already present.)
- **Audit**: `security.revoke_user` append-only row on admin revoke (reuse the existing audit log path; verify its shape).

### Pipeline position (published invoke)

`resolve_caller` runs **first** (401/429 before work) → access-policy `ensure_use` (404-as-deny) → `enforce_spend_cap` (402) → shared `_run_invoke` → stream (with periodic deny-set re-check). Mirrors the console order (`invoke.py:61-63`).

## TDD mapping (acceptance → Red test → command)

Target file: `tests/test_caller_token_denyset.py` (+ a focused `tests/test_api_tokens.py` for CRUD if it grows).

| Acceptance / FR | Red test | Command |
|-----------------|----------|---------|
| FR-010, AS1 | mint → full secret once; re-GET returns prefix-only; DB holds hash+prefix, not secret | `::test_mint_shows_secret_once_persists_hash` |
| FR-011/SC-005 | secret never in logs/trace/response after mint (capture logs; scan) | `::test_secret_never_logged_or_reechoed` |
| FR-012, AS2 | valid token invoke 200 → `DELETE` → next invoke 401 | `::test_revoke_rejects_next_invoke` |
| FR-012 (in-flight) | revoke mid-stream → stream emits error frame + stops (D3 interval) | `::test_inflight_stream_cut_on_revoke` |
| FR-013, AS3 | admin `revoke-user` → console session 401 **and** caller token 401 next request | `::test_revoke_user_cuts_session_and_token` |
| FR-024b, AS4 | Redis down during auth → 401 (fail closed), not allowed | `::test_denyset_redis_down_fails_closed` |
| FR-014 | deny-set entry TTL ≥ 3600 | `::test_denyset_ttl_at_least_one_hour` |
| FR-015, AS5 | expired token → 401 | `::test_expired_token_rejected` |
| FR-016 | mint/revoke require publisher+; revoke-user requires admin (builder→403) | `::test_rbac_on_token_and_admin_routes` |
| D1 regression | published `/v1` invoke and console `/api/v1` invoke produce identical frames for same snapshot | `::test_published_and_console_share_code_path` |

Regression: full `pytest -q` (the 8 `session_principal` callers must stay green after the deny-set check is added — D2). Migration test for `0008` mirroring `test_migration_0006`. Lint/type: `ruff check . && mypy .`.

## Project Structure

```text
services/
├── api/
│   ├── auth/denyset.py          # NEW — Redis deny-set (fail CLOSED), is_denied/deny
│   ├── publish/__init__.py      # NEW package
│   ├── publish/token_auth.py    # NEW — resolve_caller + CallerCtx
│   ├── routers/tokens.py        # NEW — mint/list/revoke + admin revoke-user
│   ├── routers/invoke.py        # CHANGE — shared _run_invoke + /v1 published route + in-flight re-check
│   ├── deps.py                  # CHANGE — deny-set in session_principal; require_caller; per-token invoke RL key
│   ├── models.py                # CHANGE — ApiToken ORM
│   └── settings.py              # CHANGE — AC_DENYSET_TTL_S, AC_DENYSET_STREAM_RECHECK_S
├── migrations/versions/
│   └── 0008_api_tokens.py       # NEW — api_tokens (RLS), NOT 0007 (taken by provider_keys)
└── tests/
    ├── test_caller_token_denyset.py  # NEW (fakeredis + tenant session)
    └── test_migration_0008.py        # NEW (mirror test_migration_0006)
```

**Structure Decision**: fill existing seams, don't fork. The published plane reuses `runtime.invoke` (D1) and 016's rate-limit engine; the deny-set is a new tiny module precisely because its fail-*closed* policy is the inverse of the rate-limiter's fail-*open*, and conflating them would be a security footgun. `services/runtime/` untouched (P6).

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
|-----------|-----------|------------------------------|
| Deny-set check added to `session_principal` (touches all 8 console-route callers) | FR-013 requires active console sessions to be cut on admin revoke, not just caller tokens | Checking only the token plane would leave a revoked human's console session alive until their token expires — defeats "immediate" revocation |
| Redis becomes a soft-SPOF for console auth (D2, fail-closed) | FR-024b mandates deny-set fail-closed; a revocation control that fails open is not a control | Fail-open deny-set rejected (a leaked token would survive a Redis blip); the SPOF is accepted per spec and its degraded behavior is the explicit subject of 021 |

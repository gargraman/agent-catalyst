# Implementation Plan: M5 Security / Tenancy — Milestone Completion

**Branch**: `feat/backend-m0-foundation` (feature dir `015-m5-security-completion`) | **Date**: 2026-11-11 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/015-m5-security-completion/spec.md`

## Summary

Close the open M5 (Security / tenancy) remainder on top of the three already-shipped M5 slices (012 IdP
bearer authN, 013 access policy, 014 provider-key custody). Scope is six user stories spanning eight security
concerns: rate limiting (F5), SSRF/egress guard (F19), the caller-token plane + immediate-revocation deny-set,
OIDC console login + session + CSRF, IdP group→members-store sync + notification projection, and the
Redis-SPOF degraded-mode + PgBouncer transaction-mode hardening. Tool approvals are explicitly **out of scope**
(deferred — depends on tool execution, itself P2).

**Technical approach.** This is a **milestone epic**; the plan's primary job is to **decompose it into six
dependency-ordered sub-features** (015a–015f), each a bounded TDD slice sized like 012/013/014. The common
thread is a **real Redis client** (the `AC_REDIS_URL` seam already exists; the client does not) which backs
rate limiting, the deny-set, and — later — durable idempotency/spend-reservation. The build fills existing
seams rather than inventing new shapes: `rate_limit()` is already a no-op dependency wired to 9 routes
(`api/deps.py:139`); the members store (`membership.role_for`/`teams_for`) is already the role/team authority
(so US5 is a *sync into* it, per the 2026-11-11 clarification); the auth-provider registry (`api/auth/`) is the
seam OIDC login plugs into. Every new authorization/safety decision is deterministic server-side code that
fails closed (Constitution P3), **except** rate limiting on a Redis outage, which fails **open + alert** by
explicit clarification (availability control, not an auth control).

## Technical Context

**Language/Version**: Python 3.12 (`services/`), TypeScript/React 19 (`frontend/`, console login touchpoints only)

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2, LangGraph/LiteLLM (runtime, untouched here); **NEW**: `redis` (async client) for the sliding-window limiter + deny-set; an OIDC client library for the authorization-code flow (see research.md)

**Storage**: Postgres 16 + pgvector (authoritative: `api_tokens`, `members`, `group_members`, `runs` ledger, RLS); **Redis** (transient: rate-limit counters, deny-set, in-flight idempotency); secrets backend (AWS Secrets Manager / LocalStack / `local`) — unchanged from 014

**Testing**: pytest (async); the project's two-layer rule does **not** add an AI eval layer here (no model/prompt behavior in this feature — deterministic security TDD only, Red→Green→Refactor per Constitution P9)

**Target Platform**: Linux server (FastAPI `api` + worker), containerized (compose dev / Helm prod)

**Project Type**: Web service (backend `services/` + existing `frontend/` console)

**Performance Goals**: Rate-limit + deny-set checks add negligible latency (one Redis round-trip each, pipelined); no new model calls; limits must engage *before* expensive work begins

**Constraints**: All auth/safety enforcement server-side + fail-closed (P3) except rate-limit-on-Redis-outage (fail-open+alert, clarified); no secret/token in logs/traces/exports (P5); session/token TTL ≤ 1h, deny-set TTL ≥ 1h; `services/runtime/` stays free of API/Redis/boto imports (P6 purity)

**Scale/Scope**: Internal pilot, single corporate deployment; 6 sub-features / 28 FRs / 8 SCs

## Decomposition into sub-features (the load-bearing output of this plan)

> **SPLIT 2026-11-11 into six real feature folders (016–021).** Each is a bounded TDD slice with its own
> `spec.md`. Run `/speckit-plan` + `/speckit-tasks` + `/speckit-implement` **per folder** in dependency order,
> not against this umbrella. P1 slices (016/017/018) are the un-built High-severity findings and land first.
> This plan/research/data-model/contracts set is the **shared design** each child references.

| Folder | Sub-feature | Pri | Depends on | New/changed surface (high level) | Primary source |
|--------|-------------|-----|-----------|----------------------------------|----------------|
| **016** | **Redis client + rate limiting (F5)** | P1 | Redis client (shared) | Fill `rate_limit()` with a Redis sliding window; limits table; `429 rate.limited` + `Retry-After`/`X-RateLimit-*`; per-token + per-principal scoping; **fail-open+alert** on Redis down | `09 §6`, F5 |
| **017** | **SSRF / egress guard (F19)** | P1 | — (independent) | App-level egress validator for HTTP tools: resolve→validate IP (block private/loopback/link-local/metadata), no internal redirects, DNS-rebind defense, per-tool allow-list | `09 §10`, F19 |
| **018** | **Caller-token plane + deny-set** | P1 | 016 (Redis) | `api/publish/token_auth.py` (`resolve_caller`), `api_tokens` mint/revoke (sha256+prefix, shown-once, `expires_at`), Redis deny-set keyed by subject+token-id, admin `revoke-user`/`force-logout`, **cut in-flight stream** on revoke, fail-closed on Redis down | `09 §2.1, §6` |
| **019** | **OIDC console login + session + CSRF** | P2 | 018 (deny-set) | OIDC authorization-code provider in `api/auth/`, server-validated session (≤1h), per-request validate + deny-set check, CSRF double-submit on state-changing routes, logout; console login touchpoints in `frontend/` | `09 §3`, `18` |
| **020** | **IdP group→members-store sync + projection** | P2 | 019 (login) | Resolve IdP groups via adapter + `workspaces.idp_binding`; **sync role/team INTO members store** (login + scheduled refresh); `group_members` projection (best-effort, fan-out only, not authZ SoR) | `18 §3–4` |
| **021** | **Redis-SPOF degraded-mode + PgBouncer txn-mode** | P3 | 016, 018 | Per-control degraded-behavior tests + blast-radius doc; PgBouncer transaction-mode wiring with no tenant-GUC leak across pooled connections | `09L §5.c`, F7/F8 adjacency |

**Critical path**: 016 (Redis) → 018 (deny-set) → 019 (OIDC) → 020 (group sync). 017 is independent and can run
in parallel. 021 is the capstone that validates degraded behavior of 016/018.

## Constitution Check

*GATE: evaluated pre-Phase 0 and re-checked post-Phase 1.*

| Principle | Status | Notes |
|-----------|--------|-------|
| **P1** Verifiable customer outcome | ✅ | Each sub-feature maps to a documented operator/publisher/admin job + measurable SCs; no LLM used (deterministic security controls). |
| **P2** Govern scope, deliver in phases | ✅ | Scope confirmed by owner 2026-11-11; tool approvals explicitly deferred (kept deferred, not dropped) — no `scope.md` phase change implied. Epic decomposed into owner-reviewable sub-features. |
| **P3** Bounded authority, deterministic fail-closed | ✅ (1 justified deviation) | All new authz/safety is server-side, fails closed — **except rate-limit on Redis outage fails open+alert** (clarified; availability control). Recorded in Complexity Tracking. |
| **P4** Earn trust with evidence | ✅ (N/A judge) | No model/prompt behavior → no eval layer; deterministic TDD only. |
| **P5** Protect data + tenant boundaries | ✅ | RLS/members/access-policy reused; token/session secrets never logged/exported; cross-tenant negatives required (SC-008). |
| **P6** Reproducible/reversible versions | ✅ | No change to snapshot/version semantics; `services/runtime/` purity preserved (no Redis/boto import there). |
| **P7** Honest about providers/cost | ✅ | No provider-contract or cost change; rate limiting protects spend/quota. |
| **P8** Operate + make failures visible | ✅ | Degraded-mode decisions explicit + tested (015f); rate-limit fail-open raises an alert; deny-set fail-closed. |
| **P9** Specify, test-first, preserve evidence | ✅ | Each sub-feature runs Red→Green→Refactor with named focused commands; no live provider calls anywhere in this feature. |

**Gate result: PASS** (one justified deviation — see Complexity Tracking).

## Project Structure

### Documentation (this feature)

```text
specs/015-m5-security-completion/
├── plan.md              # This file
├── research.md          # Phase 0 — resolved unknowns (Redis client, limiter algo, SSRF, OIDC lib, PgBouncer)
├── data-model.md        # Phase 1 — entities + migrations delta
├── quickstart.md        # Phase 1 — per-sub-feature validation guide
├── contracts/           # Phase 1 — new/changed API surface
│   ├── rate-limit.md        # 429 envelope + headers (015a)
│   ├── caller-token.md      # mint/revoke/resolve + deny-set admin actions (015c)
│   └── console-auth.md      # OIDC login/callback/logout + CSRF + group-sync (015d/015e)
└── tasks.md             # /speckit-tasks output (per sub-feature) — NOT created here
```

### Source Code (repository root)

```text
services/
├── api/
│   ├── deps.py                  # CHANGE 015a: fill rate_limit() (today a no-op at :139, wired to 9 routes)
│   ├── settings.py              # CHANGE: AC_REDIS_URL already present; add limiter/OIDC/deny-set settings
│   ├── redis_client.py          # NEW 015a: shared async Redis client + health
│   ├── ratelimit.py             # NEW 015a: sliding-window limiter + limits table + fail-open policy
│   ├── auth/
│   │   ├── base.py              # reuse: IdPProvider contract
│   │   ├── registry.py          # CHANGE 015d: register the OIDC auth-code provider
│   │   ├── oidc_provider.py     # NEW 015d: authorization-code login + session validation
│   │   ├── session.py           # NEW 015d: server-validated session (≤1h) + CSRF double-submit
│   │   └── denyset.py           # NEW 015c: Redis deny-set (subject + token-id), fail-closed
│   ├── publish/
│   │   └── token_auth.py        # NEW 015c: resolve_caller (sha256 hash lookup, revoked/expiry, rate-limit, deny-set)
│   ├── routers/
│   │   ├── tokens.py            # NEW 015c: POST/DELETE caller tokens (publisher+); admin revoke-user/force-logout
│   │   ├── auth.py              # NEW 015d: /login, /callback, /logout
│   │   └── invoke.py            # CHANGE 015c: cut in-flight stream on detected revocation
│   ├── egress.py                # NEW 015b: SSRF-safe egress validator for HTTP tools
│   ├── membership.py            # CHANGE 015e: write path — sync role/team into members store
│   ├── identity_sync.py         # NEW 015e: group→members-store sync + group_members projection
│   └── db.py                    # CHANGE 015f: PgBouncer txn-mode compatibility (GUC per-txn, no leak)
├── migrations/versions/
│   ├── 0007_api_tokens.py       # NEW/verify 015c: api_tokens (if not already present per 01)
│   └── 0008_group_members.py    # NEW 015e: group_members projection cache
└── tests/
    ├── test_rate_limit.py             # 015a
    ├── test_egress_ssrf.py            # 015b
    ├── test_caller_token_denyset.py   # 015c
    ├── test_oidc_login_session_csrf.py# 015d
    ├── test_group_sync_projection.py  # 015e
    └── test_redis_degraded_pgbouncer.py # 015f

frontend/
└── src/app/(app)/settings/page.tsx  # CHANGE 015c: wire real token mint/revoke (today mock addToken/revokeToken)
    src/lib/auth.tsx                  # CHANGE 015d: real login redirect replaces MOCK_PROFILE (where seam allows)
```

**Structure Decision**: Backend web-service layout under `services/api/` (matches 005–014). New modules are
small and single-purpose so each sub-feature is independently testable; existing seams (`rate_limit`,
`membership`, `auth/registry`, `AC_REDIS_URL`) are filled, not replaced. `services/runtime/` is untouched (P6).

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected because |
|-----------|-----------|--------------------------------------|
| **One feature spec covers six sub-features** | Owner asked to "complete M5"; the remainder is genuinely interdependent (shared Redis client, shared deny-set) and benefits from a single reconciled plan | Six independent specs would duplicate the shared-Redis/deny-set context and lose the dependency ordering; mitigated by decomposing *within* this plan and running tasks/implement per sub-feature |
| **Rate limiting fails OPEN on Redis outage** (deviates from P3 fail-closed default) | Rate limiting is an availability/cost control; failing it closed turns a cache blip into a self-inflicted total outage (clarified 2026-11-11). Deny-set + spend reservation still fail closed | Uniform fail-closed rejected because it makes Redis a hard SPOF for *all* traffic; fail-open is paired with mandatory alerting so the degradation is visible (P8), and the security-critical controls remain fail-closed |

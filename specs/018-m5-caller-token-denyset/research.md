# Phase 0 Research: 018 Caller-Token Plane + Deny-Set

Shared design lives in the umbrella: [015 research R5](../015-m5-security-completion/research.md) (caller-token plane + deny-set) and [015 research R1](../015-m5-security-completion/research.md) (Redis client — delivered by 016). This file records only the **code-grounded deltas** resolved while planning 018.

## Resolved decisions

- **D-Migration — `0008`, not `0007`.** Decision: `api_tokens` lands in a new `0008_api_tokens.py`. Rationale: `0007_provider_keys.py` already exists (feature 014); the 015 data-model's "`0007_api_tokens`" is stale. Verified `api_tokens` is absent from all current migrations (`0001`–`0007`).
- **D-DenySet error policy is the INVERSE of the rate limiter.** Decision: a separate `auth/denyset.py` module, failing **closed** on a configured-Redis error, with unset-`AC_REDIS_URL` → no-op allow (dev/test parity). Rationale: 016's `ratelimit.check` fails **open** by design (availability control); sharing the module would invite copying the wrong error policy into a security control. Alternatives considered: one shared Redis-op helper with a `fail_mode` flag — rejected as a footgun (the default would decide security posture).
- **D-Published invoke reuses one code path.** Decision: refactor `invoke.py` into a shared `_run_invoke` and add `POST /v1/agents/{id}/invoke` (token auth) alongside the console `/api/v1` route. Rationale: `08 §244-250` mandates playground + published share one body + one `runtime.invoke` (P6 — if they diverged, drift reports would evaluate code the endpoint never runs). Alternative (single auth-sniffing route) rejected — blurs the plane URL prefixes and per-agent OpenAPI narrowing.
- **D-In-flight cut via interval re-check.** Decision: re-check the deny-set every `AC_DENYSET_STREAM_RECHECK_S` (default 2s) inside the SSE generator. Rationale: per-frame Redis calls are too chatty for token-delta streams; next-request-only check violates FR-012. Trade-off: worst-case leak window = the interval.
- **D-Permission reuse.** Decision: mint/revoke → `agent.publish` (publisher+); admin revoke-user → `member.manage` (admin-only). Rationale: no `token.manage` exists and adding one drags `frontend/permissions.ts` + RBAC-drift regen into a backend slice. Alternative (dedicated `token.manage`) available as a scoped add if the owner prefers it.

## Discovered during implementation (new deltas, not in the umbrella)

- **D-CrossTenantLookup — caller-token auth needs a `SECURITY DEFINER` resolver.** Caller-token auth is inherently cross-tenant (the bearer carries no workspace; the workspace is *derived* from the matching row), but RLS hides `api_tokens` until `app.workspace_id` is set — and no existing auth path faces this (console auth always has `X-Workspace-Id`). Decision: migration `0008` ships a narrow `api_token_resolve(p_hash)` `SECURITY DEFINER` function (owned by the superuser → bypasses RLS) that returns only the single row matching a globally-unique `token_hash`; `ac_app` gets EXECUTE only and never bypasses RLS broadly. Rejected: (a) owner-engine lookup on the request path — the architecture forbids the superuser serving requests (`09 §7`); (b) a permissive RLS SELECT policy — weakens isolation for all rows; (c) encoding the workspace in the token — changes the token format/contract.
- **D-AuditDeferred — no audit infrastructure exists yet.** `08 §220` says `revoke-user` is "audited as `security.revoke_user` (append-only `audit_logs`)", but there is **no `audit_logs` table/model** in the repo. 018 emits a structured log line on revoke and **defers the durable audit row** to when audit infra lands. The 018 FRs do not require a durable audit row; the revocation itself (deny-set) is what's tested. Flagged for the owner.

## Still open for owner review

See plan.md "Decisions requiring review" D1–D5 — D2 (Redis soft-SPOF for console) and D5 (permission reuse vs. dedicated) are the ones most worth a ruling before implementation.

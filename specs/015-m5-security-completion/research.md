# Phase 0 Research: M5 Security / Tenancy Completion

All unknowns from Technical Context resolved below. No live provider calls involved; these are
infrastructure/library decisions. Each: **Decision / Rationale / Alternatives rejected**.

## R1 — Redis async client (015a, shared)

- **Decision**: Use `redis-py` ≥ 5 with its native `redis.asyncio` client, a single module-level pool
  (`api/redis_client.py`) created from the existing `AC_REDIS_URL` setting. Health exposed for `/readyz`.
- **Rationale**: `redis-py` absorbed `aioredis` and is the maintained async path; pairs with FastAPI's async
  stack; the `AC_REDIS_URL` seam already exists (default `None`). One pool avoids per-request connect cost.
- **Alternatives rejected**: `aioredis` (archived/merged into redis-py); adding a second infra (e.g. a
  dedicated rate-limit service) — overkill for a single-deployment pilot.
- **Open for plan→tasks**: behavior when `AC_REDIS_URL is None` in dev/test — the limiter must no-op-allow
  (seam parity with today) while the deny-set/token path must have a test double; keep tests Redis-optional.

## R2 — Rate-limit algorithm (015a / F5)

- **Decision**: Sliding-window counter in Redis (sorted-set or fixed-window-with-subdivision), evaluated in a
  single atomic Lua script / pipeline per request, keyed per identity (`token:{id}` for caller tokens,
  `principal:{subject}:{route-class}` for console routes). Emit `X-RateLimit-Limit/Remaining/Reset` and, on
  reject, `429 rate.limited` + `Retry-After`. Limits live in a reviewable table in config/settings.
- **Rationale**: Sliding window avoids fixed-window burst-at-boundary; a Lua script makes the check atomic
  (no check-then-set race under concurrency). Matches `09 §6`'s "Redis sliding window" intent. Header set is
  the de-facto standard clients already understand.
- **Alternatives rejected**: Token-bucket (fine, but sliding-window is simpler to reason about for "N per
  window" limits and matches the design doc); in-process limiter as primary (no cross-replica coordination —
  only acceptable as the Redis-down *fallback*, not the primary).
- **Degraded mode (clarified)**: Redis unavailable → **fail open + alert** (rate limiting only). Do **not**
  fail closed. (Deny-set/spend reservation fail closed — see R5.)

## R3 — SSRF / egress validation (015b / F19)

- **Decision**: An app-level egress guard that, for every HTTP tool call: (1) enforces the per-tool domain
  allow-list; (2) resolves the hostname and **validates every resolved IP** against a blocklist of private,
  loopback, link-local, ULA, and cloud-metadata ranges (IPv4 + IPv6); (3) **pins the connection to a
  validated IP** (connect to the checked address, not re-resolve) to defeat DNS rebinding; (4) disables or
  manually validates redirects — a redirect target is re-validated through the same guard, and any
  internal/off-allow-list target is refused. Refusals log target metadata without secrets.
- **Rationale**: This is the P1 *application-level* defense that must exist before the P2 egress sandbox/gVisor.
  Validating the connected IP (not the hostname) is the only robust defense against TOCTOU/DNS-rebinding;
  `ipaddress` stdlib classifies private/reserved ranges. Upholds Constitution P3 (prompt-injected agent ≠ SSRF
  primitive).
- **Alternatives rejected**: Hostname/domain allow-list alone (bypassed by rebinding + redirects — exactly the
  F19 finding); relying on the P2 network sandbox (not available in P1); an outbound proxy ACL (heavier infra,
  deferred to P2 egress proxy).
- **Open for plan→tasks**: the exact metadata ranges list and whether to allow link-local at all (default:
  deny all private/reserved).

## R4 — OIDC console login library + session (015d)

- **Decision**: Implement the authorization-code flow against the generic-OIDC baseline using
  `authlib` (OIDC/OAuth2 client with JWKS + discovery), plugged in as a new provider in the existing
  `api/auth/` registry. Server-validated session via a signed, HTTP-only, `Secure`, `SameSite` cookie with a
  **≤1h** lifetime; silent refresh via the IdP. CSRF defense = double-submit cookie token on state-changing
  routes. Every request validates iss/aud/exp **and** checks the deny-set (015c) before proceeding.
- **Rationale**: Reuses the IdP-adapter seam built in 012 (dev + trellix-iam providers, config-selected via
  `AC_AUTH_PROVIDER`); `authlib` is the mature, framework-agnostic choice and already supports JWKS/discovery
  the 012 verifier needs. ≤1h session matches `09 §2.1` and bounds the deny-set TTL floor. Double-submit CSRF
  matches `09 §3`.
- **Alternatives rejected**: Rolling our own OIDC code exchange / JWT verify (error-prone, reinvents 012);
  server-side session store in Redis as the *only* session mechanism (adds a Redis dependency to every console
  request and widens the SPOF — a signed cookie is stateless and the deny-set already gives immediate
  revocation); long-lived sessions (larger stolen-token blast radius, bigger deny-set retention).
- **Open for plan→tasks**: confirm the pilot IdP's discovery/JWKS endpoints (Trellix-IAM already verified in
  012) and the exact claims available (recall: Trellix-IAM emits **no** AC role/group claims → drives R-note
  below and US5).

## R5 — Deny-set + degraded-mode posture (015c / 015f)

- **Decision**: Transient Redis deny-set, keyed **by subject and by token-id**, populated by admin
  `revoke-user`/`force-logout`, TTL ≥ max token/session lifetime (≥1h). Checked on the per-request auth path
  for both console sessions and caller tokens. **Fail closed** when Redis is unavailable (reject). An in-flight
  streaming invoke is **cut** as soon as a revocation is detected (periodic/boundary check inside the stream).
  Durable per-token revocation also persists to Postgres (`api_tokens.revoked_at`) and is checked on every
  call — the deny-set unifies this with console sessions (which have no DB row).
- **Rationale**: Matches the `09 §2.1` "immediate revocation — deny-set exception" design exactly. Security
  control ⇒ fail closed (unlike the availability-oriented rate limiter). Cutting the stream realizes the
  clarified "cut immediately" decision.
- **Alternatives rejected**: Rely only on `api_tokens.revoked_at` (too slow for console sessions / stolen
  tokens — the whole reason the deny-set exists); fail-open deny-set (would let a revoked/stolen token through
  during a Redis blip — unacceptable for a security control).

## R6 — PgBouncer transaction-mode + tenant GUC (015f)

- **Decision**: Run per-request DB connections through PgBouncer in **transaction** pooling mode; set the
  tenant GUC (`app.workspace_id`) with `SET LOCAL` **inside the request transaction** (already how
  `tenant_session` scopes RLS) so it is bound to the transaction and cannot leak onto a pooled connection
  reused by another tenant. Add a negative test that proves no GUC bleed across pooled requests.
- **Rationale**: `SET LOCAL` is transaction-scoped, which is exactly what transaction-mode pooling requires;
  session-level `SET` would leak across the shared physical connection. `09L` already flagged a `SET LOCAL`
  bind nuance — this test closes it under the pooled config.
- **Alternatives rejected**: Session-mode pooling (defeats the point — one physical conn per client, poor
  scaling); session-level GUC (leaks across pooled connections — a cross-tenant isolation hole).

## R-note — US5 reconciliation (role authority)

Confirmed against code + the 2026-11-11 clarification: `membership.role_for`/`teams_for` already make the
**members store** the role/team authority under RLS (012/013), and the pilot's Trellix-IAM token exposes no AC
role/group claims. Therefore US5 is a **group→members-store sync** (write path into `members` at login + a
scheduled refresh via the adapter's `members_of` where supported) plus the best-effort `group_members`
projection for notification fan-out — **not** a live per-request group→role resolver. No new authorization
read path is introduced; the existing `require_permission`/access-policy stack consumes the synced member rows
unchanged.

## Resolved unknowns summary

| Unknown (Technical Context) | Resolution |
|-----------------------------|------------|
| Redis async client | redis-py `redis.asyncio`, single pool from `AC_REDIS_URL` (R1) |
| Rate-limit algorithm | Redis sliding window, atomic Lua, standard 429 + headers; fail-open on outage (R2) |
| SSRF validation mechanism | resolve→validate-every-IP→pin-connection→re-validate-redirects (R3) |
| OIDC client library | authlib auth-code via the `api/auth/` registry; signed ≤1h cookie + double-submit CSRF (R4) |
| Deny-set posture | subject+token-id keyed, fail-closed, cut in-flight stream (R5) |
| PgBouncer mode | transaction mode + `SET LOCAL` GUC, no-leak negative test (R6) |

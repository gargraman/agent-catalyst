# Phase 1 Data Model: M5 Security / Tenancy Completion

Scope: the persistent + transient data this feature adds or touches. Authoritative records live in Postgres
(under RLS); transient security state lives in Redis. Design source: `docs/backend/01-data-model.md` (`§4.7`
`api_tokens`; `18 §4` `group_members`), `09 §2.1/§6`. Tool-approval tables are **out of scope** (deferred).

## Postgres entities

### `api_tokens` (015c) — caller tokens for published invoke

WorkspaceScoped (RLS). Verify against existing migrations; create `0007_api_tokens` if absent.

| Field | Type | Notes |
|-------|------|-------|
| `id` | uuid pk | |
| `workspace_id` | uuid fk → workspaces | RLS tenant key; invoke is pinned to this workspace |
| `agent_id` | uuid fk → agents | one token → one published endpoint (01 §4.7) |
| `name` | text | human label |
| `token_hash` | text **unique** | sha256 of the secret; the raw secret is NEVER stored (FR-010) |
| `prefix` | text | short display prefix (e.g. `ac_live_7Q2…`), safe to show |
| `scopes` | jsonb | `{invoke: true, teams: [...]}` — caller teams source for access policy (09 §5) |
| `created_at` | timestamptz | |
| `last_used_at` | timestamptz null | best-effort |
| `expires_at` | timestamptz null | optional expiry (FR-015) |
| `revoked_at` | timestamptz null | set on revoke → immediate reject (FR-012); authoritative record |

- **Validation**: `token_hash` unique (F17 corollary); exactly one `agent_id`; `expires_at` (if set) > `created_at`.
- **Lifecycle**: `mint` (secret shown once) → active → `revoked` (`revoked_at` set) **or** expired (`expires_at` past). No un-revoke.
- **Secret handling**: raw secret returned once on mint; only `token_hash` + `prefix` persist; never logged/exported (FR-011, SC-005).

### `members` (existing, 006/0006) — CHANGE: write path (015e)

No schema change. US5 adds a **sync writer** that upserts `console_role` and `teams` from the IdP group mapping
at login + scheduled refresh. `role_for`/`teams_for` (reads) are unchanged and remain the authority.

### `group_members` (015e) — notification fan-out projection (read-model cache)

**Not an authorization source of record** (18 §4). Create `0008_group_members`.

| Field | Type | Notes |
|-------|------|-------|
| `group_ref` | text | native IdP ref (matches `workspaces.idp_binding` values: name/GUID/attr) |
| `subject` | text | OIDC subject |
| `source` | text | `members_of` (scheduled enumeration) \| `login_seen` (upserted on login) |
| `seen_at` | timestamptz | freshness |
| pk | (`group_ref`, `subject`) | |

- **Validation**: cache only; may be stale; MUST NOT be read to grant access (only to fan out notifications).

### `workspaces.idp_binding` (existing jsonb, 18 §3) — CONSUME (015e)

Per-workspace role→native-group and team→native-group map. Consumed by the sync; no new column. Supports name
(Okta), object-GUID (Entra), and attribute-value (SAML) refs — no name-prefix assumption (FR-021).

## Redis (transient) structures

| Key pattern | Purpose | Sub-feature | TTL / eviction | Degraded (Redis down) |
|-------------|---------|-------------|----------------|-----------------------|
| `rl:token:{token_id}` / `rl:prin:{subject}:{route_class}` | sliding-window rate-limit counters | 015a | window-scoped | **fail open + alert** |
| `deny:sub:{subject}` / `deny:tok:{token_id}` | immediate-revocation deny-set | 015c | ≥ 1h (≥ max token/session TTL) | **fail closed** |
| `idem:{workspace}:{key}` | durable idempotency replay (future; in-process guard today) | 015f-adjacent | short | fail closed (retryable) |

## Console session (015d) — stateless signed token (not a DB row)

Signed, HTTP-only, `Secure`, `SameSite` cookie; claims: subject, iss, aud, exp (≤1h), csrf-binding. Validated
per request (sig + iss/aud/exp) **and** checked against `deny:sub:{subject}` before the handler runs. No
server-side session table (stateless; revocation via deny-set). CSRF: double-submit cookie token required on
state-changing routes.

## Egress policy (015b) — config, not a table

Per-tool: `allowlist_domains: [...]`; global blocklist = private/loopback/link-local/ULA/metadata IP ranges
(IPv4+IPv6). Evaluated at call time against the **resolved + connected** IP. No new persistence.

## Entity → requirement → sub-feature map

| Entity / structure | Requirements | Sub-feature |
|--------------------|--------------|-------------|
| `api_tokens` | FR-010..012, FR-015, FR-016 | 015c |
| deny-set (Redis) | FR-013, FR-014, FR-012 (in-flight cut) | 015c |
| rate-limit counters (Redis) | FR-001..005, FR-024 (fail-open) | 015a |
| egress policy | FR-006..009 | 015b |
| console session | FR-017..020 | 015d |
| `members` (sync writer) + `idp_binding` | FR-021, FR-022 | 015e |
| `group_members` | FR-023 | 015e |
| PgBouncer txn GUC | FR-026 | 015f |

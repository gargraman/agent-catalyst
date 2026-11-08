# Data Model — Pluggable IdP Bearer-Token Auth (feature 012)

No database schema change (no migration). "Entities" here are in-memory/config structures at the API edge plus
one read of the existing `members` table.

## Principal (extended — `api/deps.py`)

The resolved caller every dependency consumes. Extended, not reshaped, so downstream code is unchanged.

| Field | Type | Notes |
|---|---|---|
| `subject` | `str` | token `sub` (Trellix IAM: `== client_id`) / dev header subject |
| `workspace_claims` | `dict[str,str]` | `ws_id → role`. **dev provider**: from header. **real provider**: filled per-request by `resolve_workspace` from the membership read (starts empty). |
| `scopes` | `tuple[str,...]` | NEW. IdP scopes (e.g. `xdr.alr.rw`). Carried for audit/future; NOT mapped to AC permissions this slice (FR-013). |
| `issuer` | `str \| None` | NEW. Verified token issuer / `"dev"` for the dev provider. |

Validation: `subject` non-empty. `workspace_claims` values must be known RBAC roles when present (unknown → no
permission, fail-closed via existing `has_permission`).

## Provider verify output

Both providers' `verify(request)` return a `Principal` **directly** (the real provider sets `workspace_claims={}`
and fills `scopes`/`issuer`; the dev provider fills `workspace_claims` from the header). An earlier design had a
separate intermediate `AuthnResult` dataclass, but it was unused and removed (code-review 2026-11-09 / T027) —
the normalized output is just `Principal`.

## IdPProvider (config + behavior — `api/auth/`)

| Field / member | Type | Notes |
|---|---|---|
| `id` | `str` | `"dev"` \| `"trellix-iam"` (registry key) |
| `verify(request) -> Principal` | async | raises `AuthError` on any failure (FR-005, FR-010) |
| `issuer` | `str` | (jwt providers) expected `iss` |
| `audience` | `str` | (jwt providers) expected `aud` (Trellix: `mcafee`) |
| `jwks_uri` | `str` | (jwt providers) `https://preprod.iam.cloud.trellix.com/iam/v1.0/jwks` |
| `allowed_algs` | `tuple[str,...]` | default `("RS256",)`; `alg=none`/HS* rejected |
| `clock_skew_s` | `int` | leeway for `exp`/`nbf`/`iat`; bounded (e.g. ≤120) |

## Settings additions (`api/settings.py`)

| Setting | Env alias | Default | Notes |
|---|---|---|---|
| `env` | `AC_ENV` | `dev` | `prod` disables the dev provider (FR-008, R5) |
| `auth_provider` | `AC_AUTH_PROVIDER` | `dev` | active provider id |
| `iam_issuer` | `AC_IAM_ISSUER` | preprod issuer | trellix provider |
| `iam_audience` | `AC_IAM_AUDIENCE` | `mcafee` | trellix provider |
| `iam_jwks_uri` | `AC_IAM_JWKS_URI` | preprod jwks | trellix provider |
| `iam_allowed_algs` | `AC_IAM_ALGS` | `RS256` | csv |
| `iam_clock_skew_s` | `AC_IAM_CLOCK_SKEW_S` | `60` | bounded |

All dev defaults are non-secret (public metadata); no secret lives in settings.

## JWKS key set (cached — `jwt_provider.py`)

Public keys by `kid`, fetched from `jwks_uri`, cached with bounded TTL. On unknown `kid`: refresh once, else
reject `401` (FR-017). Fetch failure → reject (never accept unverified). (PyJWT `PyJWKClient` provides this; key
source is injectable so tests supply a local key set with no network — FR-015.)

## Membership read (existing `members` — `api/membership.py`)

Read-only use of the feature-006 table. Within `tenant_session(ws)` (RLS GUC=ws):
`SELECT console_role FROM members WHERE subject = :subject` → role `str` or `None`.
`None` → caller is not a member of `ws` → `AccessDenied` (403). Columns used: `workspace_id` (via RLS),
`subject`, `console_role`. No writes, no schema change.

## State / lifecycle

- **Token**: stateless per request; verified then discarded (never persisted/logged).
- **JWKS cache**: the only mutable state — populated on first use, refreshed on TTL expiry or unknown `kid`
  (at most one refresh per miss). No per-tenant state.

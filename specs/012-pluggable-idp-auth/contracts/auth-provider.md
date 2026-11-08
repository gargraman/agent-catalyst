# Contract — IdP Provider Seam + Bearer AuthN (feature 012)

This feature's "interface" is an internal seam plus an inbound HTTP header contract — not a new REST endpoint.

## 1. Inbound request contract

- **Real providers**: caller sends `Authorization: Bearer <jwt>` + `X-Workspace-Id: <ws_id>`.
- **Dev provider** (non-prod only): caller sends `X-Dev-Principal: <json>` + `X-Workspace-Id` (unchanged from today).
- Workspace may also come from `?workspace=` (existing behavior retained).

## 2. Provider protocol (`api/auth/base.py`)

```python
class IdPProvider(Protocol):
    id: str
    async def verify(self, request: Request) -> Principal: ...
    # MUST raise AuthError (→ 401) on: missing/malformed token, bad signature,
    # iss/aud mismatch, expired/not-yet-valid (beyond skew), disallowed alg,
    # unknown kid after one refresh, or JWKS unavailable. MUST NOT return a
    # partial/default Principal on failure (FR-010). MUST NOT log the raw token (FR-011).
```

- `get_active_provider(settings) -> IdPProvider` (registry): returns the provider named by `AC_AUTH_PROVIDER`;
  **raises/startup-fails** if it names `dev` while `AC_ENV=prod`, or names an unknown/misconfigured provider
  (fail-closed, R5/FR-008). No secret in the error.

## 3. Response / error contract

- Success: request proceeds; a `Principal` is available to all existing dependencies (shape per data-model).
- Failure: `401` using the existing error envelope (`errors.AuthRequired` → `{"error":{"code":"auth.required",...}}`).
  Body carries a non-sensitive reason only (e.g. `expired`, `bad_signature`, `aud_mismatch`) — never the token.
- Authorization failures remain the existing codes: `403 access.denied` (no membership / insufficient role),
  `400 input.invalid` (no workspace selected) — unchanged (FR-012).

## 4. JWKS / verification parameters (trellix-iam)

| Param | Value |
|---|---|
| issuer | `https://preprod.iam.cloud.trellix.com/iam/v1.0` |
| audience | `mcafee` |
| jwks_uri | `https://preprod.iam.cloud.trellix.com/iam/v1.0/jwks` |
| algorithms | `["RS256"]` (explicit; token-header alg never trusted) |
| leeway | configurable clock skew (default 60s, bounded) |

## 5. Contract test obligations (what the suite must assert)

Verification (unit, local keypair — no network, no secret):
1. valid signed token (correct iss/aud, in-date, RS256) → `Principal{subject, scopes, issuer}`.
2. tampered signature → `AuthError`/401.
3. `aud` mismatch → 401. 4. `iss` mismatch → 401.
5. `exp` in past (beyond skew) → 401. 6. `nbf`/`iat` in future (beyond skew) → 401.
7. `alg=none` → 401. 8. HS256 where RS256 expected → 401.
9. unknown `kid` after one refresh → 401. 10. missing/malformed Authorization → 401.
11. no token/secret value appears in the raised error or any log record.

Registry/profile (unit):
12. `AC_AUTH_PROVIDER=dev` + `AC_ENV=prod` → fails closed (no dev provider).
13. provider swap changes which tokens verify with **no** router/permission edits (SC-003).

Membership authZ (integration, live Postgres):
14. real provider + seeded `members` row (subject, ws, role) → role resolved, `require_permission` passes/denies per role.
15. real provider + **no** membership row for ws → `403` (no existence leak), before resource load.
16. cross-tenant: subject with a membership in ws-A cannot act on ws-B → `403`.

Purity: `runtime/` + `evals/` import no auth/jwt/http modules (existing purity test stays green).

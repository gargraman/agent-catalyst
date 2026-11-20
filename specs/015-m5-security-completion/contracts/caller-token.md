# Contract: Caller-token plane + immediate revocation (015c)

New routes under the console API (authZ: mint/revoke = publisher+; subject revoke/force-logout = admin). Caller
tokens authenticate the published-invoke plane (execute-only role `executer`, tenant pinned by the token).

## Mint a caller token

```
POST /v1/agents/{agent_id}/tokens        (require publisher+)
Body: { "name": string, "expires_at"?: ISO8601, "teams"?: string[] }

201 Created
{
  "id": "tok_…",
  "prefix": "ac_live_7Q2…",
  "secret": "ac_live_7Q2………",   // FULL secret — returned EXACTLY ONCE, never again (FR-010)
  "agent_id": "…", "name": "…", "created_at": "…", "expires_at": null
}
```

- Server stores only `sha256(secret)` + `prefix` (FR-010). `secret` never appears in any subsequent response,
  log, trace, or export (FR-011, SC-005).

## List / revoke

```
GET    /v1/agents/{agent_id}/tokens      → masked roster (prefix only, no secret)
DELETE /v1/tokens/{token_id}             (require publisher+)  → 204; sets revoked_at; next invoke 401 (FR-012)
```

## Admin immediate revocation (deny-set)

```
POST /v1/admin/revoke-user               (require admin)
Body: { "subject": string }             // or { "token_id": string }

204 No Content
```

- Adds `deny:sub:{subject}` (and/or `deny:tok:{token_id}`) with TTL ≥ 1h (FR-013/014).
- Effect: the subject's active console session AND live caller tokens are rejected on their **next** request
  (401), and any **in-flight streaming invoke is cut** (FR-012).

## Caller auth resolution (per invoke, `resolve_caller`)

Order (fail-closed at each step):
1. Look up `sha256(bearer)` in `api_tokens`; unknown/`revoked_at` set → `401`.
2. `expires_at` past → `401` (FR-015).
3. Deny-set check (`deny:tok:{id}`, `deny:sub:{subject}`) → match → `401`. **Redis down → `401` (fail closed).**
4. `rate_limit(token_id)` (015a) → over → `429`.
5. Build `CallerCtx(workspace_id, token_id, role="executer", teams=scopes.teams)`.

## Error envelopes

- `401` → `{ "error": { "code": "auth.unauthorized", ... } }` (no token value echoed).
- `429` → `rate.limited` (see rate-limit.md).

## Acceptance (maps to FR-010..016, SC-004/005)

1. Mint → secret shown once; re-fetch never returns it; only hash+prefix persisted.
2. Valid token invokes `200`; after `DELETE` → next invoke `401`.
3. `revoke-user` → active session + caller token both `401` next request; in-flight stream cut.
4. Redis down during auth → `401` (fail closed), not allowed through.
5. Expired token → `401`.

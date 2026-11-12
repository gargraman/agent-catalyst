# Contract: Caller-token plane + deny-set (018)

The authoritative contract is the shared umbrella: **[015 contracts/caller-token.md](../../015-m5-security-completion/contracts/caller-token.md)** — mint/list/revoke routes, admin `revoke-user`, and the `resolve_caller` resolution order.

## 018-specific bindings (grounded to code)

- **Routes** (per `08 §173-175, §220, §247`):
  - `POST /api/v1/agents/{id}/tokens` · `GET /api/v1/agents/{id}/tokens` · `DELETE /api/v1/tokens/{id}` → `require_permission("agent.publish")` (publisher+).
  - `POST /api/v1/org/revoke-user` → `require_permission("member.manage")` (admin). Body `{subject}` and/or `{token_id}`. Writes the deny-set + `security.revoke_user` audit row.
  - `POST /v1/agents/{id}/invoke` (published plane, prefix `/v1`) → `resolve_caller`, `source="published"`.
- **`resolve_caller` order** (fail-closed each step): sha256 lookup → revoked/expired → deny-set (Redis down ⇒ 401) → per-token rate limit (`rl:token:{id}`) → build `CallerCtx(workspace_id, token_id, subject, role="executer", teams)`.
- **Error envelopes**: `401 auth.unauthorized` (no token echoed), `429 rate.limited` (headers via `AppError.headers`), `403 access.denied` (RBAC).
- **In-flight cut**: the SSE generator re-checks the deny-set every `AC_DENYSET_STREAM_RECHECK_S`; on a hit → `error` frame (`auth.unauthorized`) then stop.

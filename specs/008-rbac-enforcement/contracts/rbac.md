# Contract — RBAC Enforcement (M5)

Server-side authorization contract. No new endpoints; this governs how **every** existing workspace-scoped endpoint authorizes a caller. No request/response schema changes — only an added `403 access.denied` possibility on operations the caller's role may not perform.

## 1. Authorization order (per request)

1. **Authn** — `session_principal`: no principal → `401 auth.required`.
2. **Workspace selection + membership** — `resolve_workspace`: no selection → `400 input.invalid`; selected workspace not in the caller's claims → `403 access.denied` (membership).
3. **Permission (NEW)** — `require_permission(P)`: `role = claims[ws]`; if `not has_permission(role, P)` → `403 access.denied`, raised **before the handler runs** (no resource load, no 404/existence distinction, no mutation, no idempotency side effect).
4. **Handler** — runs under the RLS tenant session; normal `200/201/204/404/409/422` apply.

## 2. Roles & permissions

Four roles `viewer < builder < publisher < admin`; 19 permissions; `ROLE_PERMISSIONS` generated from `frontend/src/lib/permissions.ts` (see data-model.md). `has_permission(role, p)` is fail-closed: an unknown/empty role has **no** permissions.

## 3. Operation → required permission

The full 47-route table is in data-model.md. Tier summary:

| Tier | Permissions (added at this tier) | Example operations |
|---|---|---|
| viewer | `view`, `playground.use` | all GET/list, playground invoke |
| builder | `agent.create/edit`, `eval.run`, `*.manage` (knowledge/tool/mcp/skill/channel) | create/edit agents, run evals, manage tools/MCP/skills/KB |
| publisher | `agent.publish`, `tool.approve` | publish a version, approve tool calls |
| admin | `agent.delete`, `member.manage`, `settings.manage`, `access.manage`, `key.manage`, `eval.govern` | manage members, workspace settings |

## 4. Error envelope

`403 access.denied` → `{"error": {"code": "access.denied", "message": "...", "detail": {"required_permission": "<perm>", "role": "<role>"}}}`. No cross-tenant or resource-existence information is included (an under-privileged caller cannot distinguish an existing from a missing resource — same 403).

## 5. Invariants (tested)

- **Deny-by-default**: every `/api/v1` route declares a required permission; a coverage test fails on any unannotated route.
- **No drift**: the server `ROLE_PERMISSIONS` equals a fresh parse of `permissions.ts` (drift test).
- **Fail-closed**: an unrecognized role is denied every operation, including reads.
- **Tenancy preserved**: membership + RLS still apply; role enforcement never widens cross-tenant visibility.
- **No new deps, no migration; `runtime/` + `evals/` untouched.**

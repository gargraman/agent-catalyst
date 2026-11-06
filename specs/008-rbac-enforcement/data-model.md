# Data Model — RBAC Enforcement Slice (M5)

No database schema change (no migration). These are in-memory value types + the generated permission data + the full operation→permission contract.

## Value types — `services/api/permissions.py`

### Role
`"viewer" | "builder" | "publisher" | "admin"` (ordered by privilege). Sourced from `principal.workspace_claims[ws]` (D3). An unrecognized string is a valid input but maps to zero permissions (fail-closed).

### Permission
The 19 names mirrored from `permissions.ts`: `view`, `playground.use`, `agent.create`, `agent.edit`, `eval.run`, `eval.author`, `eval.govern`, `knowledge.manage`, `tool.manage`, `mcp.manage`, `skill.manage`, `channel.manage`, `agent.publish`, `tool.approve`, `agent.delete`, `key.manage`, `member.manage`, `access.manage`, `settings.manage`.

### ROLE_PERMISSIONS
`dict[Role, frozenset[Permission]]`, loaded from the generated `rbac_permissions.json`. Expected (mirrors `permissions.ts`):
- **viewer**: `view`, `playground.use`
- **builder**: viewer + `agent.create`, `agent.edit`, `eval.run`, `eval.author`, `knowledge.manage`, `tool.manage`, `mcp.manage`, `skill.manage`, `channel.manage`
- **publisher**: builder + `agent.publish`, `tool.approve`
- **admin**: publisher + `agent.delete`, `key.manage`, `member.manage`, `access.manage`, `settings.manage`, `eval.govern`

### `has_permission(role, permission) -> bool`
`permission in ROLE_PERMISSIONS.get(role, frozenset())` — fail-closed for unknown role.

## Generated artifact — `services/api/rbac_permissions.json`
`{ "viewer": [...], "builder": [...], "publisher": [...], "admin": [...] }`, produced by `scripts/gen_rbac.py` from `permissions.ts` (D4). Committed; regenerated when `permissions.ts` changes; a drift test asserts the committed file matches a fresh parse.

## Dependency — `require_permission(permission)` (`services/api/deps.py`)
Returns a dependency callable with attribute `required_permission = permission` (for coverage introspection, D2). Resolves `session_principal` + `resolve_workspace`; raises `AccessDenied("insufficient role", detail={"required_permission": permission, "role": role})` unless `has_permission(role, permission)`.

## Operation → required permission (the full contract, all 47 routes)

| Router | Route | Permission |
|---|---|---|
| agents | `POST /agents` | `agent.create` |
| agents | `GET /agents`, `GET /agents/{id}` | `view` |
| agents | `PATCH /agents/{id}`, `POST /agents/{id}/versions` | `agent.edit` |
| invoke | `POST /agents/{id}/invoke` | `playground.use` |
| evals | `POST /agents/{id}/eval-runs` | `eval.run` |
| evals | `POST /agents/{id}/publish` | `agent.publish` |
| tools | `POST /tools`, `PATCH /tools/{id}`, `POST /tools/{id}/archive`, `POST /tools/{id}/restore` | `tool.manage` |
| tools | `GET /tools`, `GET /tools/{id}` | `view` |
| tools | `POST /mcp-servers`, `PATCH /mcp-servers/{id}`, `POST /mcp-servers/{id}/archive`, `POST /mcp-servers/{id}/tools`, `PATCH /mcp-tools/{id}` | `mcp.manage` |
| tools | `GET /mcp-servers`, `GET /mcp-servers/{id}` | `view` |
| skills | `POST /skills`, `PATCH /skills/{id}`, `POST /skills/{id}/publish`, `POST /skills/{id}/archive`, `POST /skills/{id}/restore` | `skill.manage` |
| skills | `GET /skills`, `GET /skills/{id}` | `view` |
| knowledge | `POST /knowledge-bases`, `PATCH /knowledge-bases/{id}`, `POST /knowledge-bases/{id}/archive`, `POST /knowledge-bases/{id}/restore` | `knowledge.manage` |
| knowledge | `GET /knowledge-bases`, `GET /knowledge-bases/{id}`, `GET /ingestion-jobs` | `view` |
| workspaces | `GET /workspace/settings` | `view` |
| workspaces | `PATCH /workspace/settings`, `PATCH /workspace` | `settings.manage` |
| members | `GET /members` | `view` |
| members | `POST /members`, `PATCH /members/{id}`, `DELETE /members/{id}` | `member.manage` |
| runs | `GET /runs`, `GET /runs/{id}`, `GET /metrics/dashboard`, `GET /metrics/runs`, `GET /metrics/evals` | `view` |

Count: 47 routes, all annotated → deny-by-default coverage holds.

## Authorization decision flow

```
request → session_principal (401 if none)
        → resolve_workspace (400 no ws; 403 if ws not in claims — membership)
        → require_permission(P): role = claims[ws]; has_permission(role, P)?
             no  → 403 access.denied (BEFORE handler: no resource load, no 404 distinction, no mutation)
             yes → handler runs under tenant_db (RLS) → normal 200/404/409/…
```

Composition: membership (P5) → role (P3) → RLS (P5). Each is independent defense in depth.

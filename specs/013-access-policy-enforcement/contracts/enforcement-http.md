# Contract: HTTP Enforcement Behavior

How the pure evaluator is applied at each API boundary, and the resulting status codes. Enforcement order per
request: **RLS tenant session → RBAC `require_permission` → access-policy evaluation**. Admin bypasses only the
access-policy layer.

## Per-boundary behavior (applies to each in-scope family)

| Boundary | Behavior | Status on deny |
|---|---|---|
| `GET /…` (list) | Fetch the RLS-scoped page, then omit every row where `can_use(policy, principal)` is false. Admin sees all. | n/a (silent omission) |
| `GET /…/{id}` (read) | Load row (RLS). If not found → 404. If `not can_use` → **404** (indistinguishable from absent). | `404 not_found` |
| `PATCH /…/{id}`, `POST …/publish`, `…/archive`, `…/restore` (manage) | Load row (RLS). If not found → 404. If `not can_use` → **404**. Else if `not can_manage` → **403**. | `404 not_found` (invisible) → else `403 access.denied` |
| `POST /…/{id}/invoke` (use, agents) | Load agent (RLS). If not found → 404. If `not can_use` → **404**, *before* any provider call or Run row. | `404 not_found` |
| `POST /…` (create) | If body `access_policy.visibility == "private"` and no `owner`, set `owner = principal.subject`. Validate/normalize the policy; reject malformed → `400 input.invalid`. | `400 input.invalid` (malformed policy) |

Notes:
- The existing RBAC gate (`require_permission`) still runs first and raises `403 access.denied` for a role that
  lacks the route permission — this is resource-independent and leaks nothing about a specific row.
- `can_use`/`can_manage` receive the `PolicyPrincipal` built by the new `access_principal` dependency
  (role from `workspace_claims[ws]`, teams from `members.teams` under RLS, subject from the principal).
- Error envelope is the existing `{"error":{"code","message","detail"?}}` (api/errors.py). The `404` reuses
  `NotFound`; the `403` reuses `AccessDenied` — no new error classes required.

## Members teams surface (FR-017 server side)

| Endpoint | Change |
|---|---|
| `POST /api/v1/members`, `PATCH /api/v1/members/{id}` | accept `teams: string[]` (default `[]`) |
| `GET /api/v1/members`, `GET /api/v1/members/{id}` | `MemberOut` surfaces `teams` |

Teams edits require the existing `members.manage` permission (no new permission). Setting teams is how an admin
grants a member access to restricted resources.

## No-leak acceptance (the headline tests)

For a `restricted` or `private` resource R a principal P cannot use:
1. `GET list` for P does not contain R.
2. `GET /R` for P returns `404` with the same body shape as a genuinely-absent id — **never 403**.
3. `PATCH /R` / `publish` / `archive` for P returns `404` — never 403, never a stale-ETag hint that R exists.
4. `invoke R` for P returns `404` and writes no Run row and makes no provider call.
5. A principal Q who *can* use R but cannot manage it: `GET /R` → 200; `PATCH /R` → **403**.
6. An admin: all of the above → success, regardless of policy (including empty/malformed).

## Observability (FR-014)

On a policy deny, log one structured line: capability (use/manage), visibility tier, resource family + id,
principal subject, workspace id — **no token, no secret, no policy owner PII beyond the subject already in scope**.
The existing `ac_requests_refused_total{code}` metric counts the resulting `403`s; `404`s are not counted as
refusals (they are indistinguishable from absence by design).

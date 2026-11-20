# Contract: Access-Policy Evaluation (pure decision table)

The backend evaluator (`api/access_policy.py`) MUST produce exactly these decisions. This table mirrors
`frontend/src/lib/access-policy.ts` (`canUse` / `canManage` / `matches` / `isOwner`) verbatim. A JSON fixture of
these rows is the shared parity artifact (FR-013 / SC-003).

## Functions

- `can_use(policy, principal) -> bool`
- `can_manage(policy, principal) -> bool`

`principal` = `PolicyPrincipal{ role, teams, subject }` where `role ∈ {viewer,builder,publisher,admin,executer}`.
`is_executer ≡ role == "executer"`.

## Core rules (apply in order)

1. **Ungoverned**: `policy` missing OR `visibility == "workspace"` → **allow** (both use and manage).
2. **Admin bypass**: `role == "admin"` → **allow** (both). (Admin is a console claim; never an executer.)
3. **Private**: `visibility == "private"` → allow iff `subject` is non-empty AND equals `policy.owner`
   (non-empty). Executers (`subject is None`) → **deny**. (Same for use and manage.)
4. **Restricted**: delegate to `matches(roles, teams, principal)` with the capability's lists
   (`useRoles/useTeams` for use; `manageRoles/manageTeams` for manage).

## `matches(roles, teams, principal)` — the restricted rule

| Condition | Result |
|---|---|
| `roles` empty AND `teams` empty | **deny** (empty restriction grants no one — fail closed) |
| `principal.role == "executer"` | allow iff `teams` non-empty AND `principal.teams ∩ teams ≠ ∅` (team-only; a role-only policy denies executers) |
| console principal | allow iff `roleOk AND teamOk`, where `roleOk = roles empty OR principal.role ∈ roles`, `teamOk = teams empty OR principal.teams ∩ teams ≠ ∅` |

## Decision matrix (representative fixture rows)

Legend: policy shape under `restricted` is written as `use=(roles;teams)` / for private `owner=X`.

| # | visibility | policy shape | principal (role, teams, subject) | can_use | can_manage |
|---|---|---|---|---|---|
| 1 | workspace | — | (viewer, [], u1) | ✅ | ✅ |
| 2 | workspace | — | (executer, [], None) | ✅ | ✅ |
| 3 | restricted | use=(["publisher"];[]) manage=(["publisher"];[]) | (publisher, [], u1) | ✅ | ✅ |
| 4 | restricted | use=(["publisher"];[]) | (builder, [], u2) | ❌ | ❌ |
| 5 | restricted | use=([];["SOC"]) | (viewer, ["SOC"], u3) | ✅ | (per manage lists) |
| 6 | restricted | use=([];["SOC"]) | (viewer, ["GRC"], u4) | ❌ | ❌ |
| 7 | restricted | use=(["builder"];["SOC"]) | (builder, ["GRC"], u5) | ❌ (role ok, team no) | ❌ |
| 8 | restricted | use=(["builder"];["SOC"]) | (builder, ["SOC"], u6) | ✅ | (per manage) |
| 9 | restricted | use=([];[]) (empty) | (publisher, ["SOC"], u7) | ❌ (fail closed) | ❌ |
| 10 | restricted | use=([];[]) | (admin, [], a1) | ✅ (admin bypass) | ✅ |
| 11 | restricted | use=([];["SOC"]) | (executer, ["SOC"], None) | ✅ (team-only) | n/a |
| 12 | restricted | use=(["builder"];[]) | (executer, ["SOC"], None) | ❌ (role-only denies executer) | n/a |
| 13 | restricted | use=([];["SOC"]) | (executer, ["GRC"], None) | ❌ (team mismatch) | n/a |
| 14 | private | owner=u1 | (builder, [], u1) | ✅ | ✅ |
| 15 | private | owner=u1 | (publisher, [], u2) | ❌ | ❌ |
| 16 | private | owner=u1 | (admin, [], a1) | ✅ | ✅ |
| 17 | private | owner=u1 | (executer, ["SOC"], None) | ❌ | ❌ |
| 18 | private | owner="" (blank) | (builder, [], u1) | ❌ (no owner match) | ❌ |
| 19 | private | owner="" | (admin, [], a1) | ✅ | ✅ |
| 20 | garbage | visibility="weird" | (publisher, ["SOC"], u1) | ❌ (most-restrictive) | ❌ |
| 21 | garbage | visibility="weird" | (admin, [], a1) | ✅ (admin bypass) | ✅ |

> MANAGE rows follow the identical structure using `manageRoles/manageTeams`; the fixture includes manage
> variants for rows 3–10 and 14–21. Executer MANAGE is never exercised (callers never manage) — manage for an
> executer is defined as **deny** for completeness.

## Invariants the unit test MUST assert

- Every fixture row's `can_use`/`can_manage` matches the table.
- Admin is always allowed regardless of policy shape (rows 10, 16, 19, 21).
- An empty restricted side denies every non-admin (row 9) — never a silent open.
- Executers are gated on teams alone and never satisfy `private` (rows 11–13, 17).
- A malformed/unknown visibility denies non-admins (rows 20–21).

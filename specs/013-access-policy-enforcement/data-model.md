# Data Model: Per-Resource Access-Policy Enforcement

## Entity: Access Policy (embedded JSON on a governed resource)

Stored in the `access_policy jsonb` column. Shape mirrors `frontend/src/lib/access-policy.ts` `AccessPolicy`.

| Field | Type | Rules |
|---|---|---|
| `visibility` | `"workspace" \| "restricted" \| "private"` | Required. Unknown/garbage value → treated as most-restrictive (deny non-admin). Default `"workspace"`. |
| `useRoles` | `string[]` | Roles permitted to USE under `restricted`. Empty = unconstrained on role (AND-combined with teams). |
| `useTeams` | `string[]` | Teams permitted to USE under `restricted`. Empty = unconstrained on team. |
| `manageRoles` | `string[]` | Roles permitted to MANAGE under `restricted`. Same empty-semantics. |
| `manageTeams` | `string[]` | Teams permitted to MANAGE under `restricted`. |
| `owner` | `string?` | Set only when `visibility === "private"`: the owner's subject. Blank/missing → only admins reach it. |

**Field naming (FR-018)**: the stored JSON and the API schema use the **exact camelCase keys of
`access-policy.ts`** (`visibility`, `useRoles`, `useTeams`, `manageRoles`, `manageTeams`, `owner`) — one shape,
no FE↔BE translation. A typed Pydantic model `schemas/access_policy.py::AccessPolicy` (camelCase field names)
represents it so OpenAPI/the generated client are typed (not an opaque object). The evaluator reads these keys.

**Normalization (defensive, fail-closed)**: a missing/empty object → `{"visibility":"workspace"}`. Missing list
fields → `[]`. `restricted` with all four lists empty grants no one on either capability. These rules live in the
evaluator so every family inherits them identically.

## Entity: PolicyPrincipal (in-memory, per request — not persisted)

The evaluator's view of the caller. Built from the resolved request principal + membership read.

| Field | Type | Source |
|---|---|---|
| `role` | `"viewer" \| "builder" \| "publisher" \| "admin" \| "executer"` | `principal.workspace_claims[ws]` for a console caller; `"executer"` for a runtime/token caller (no console role). |
| `teams` | `str[]` | `members.teams` for the caller's subject under RLS (console caller); supplied by the token/channel plane for an `executer` (future — synthesized in tests here). |
| `subject` | `str \| None` | `principal.subject` for a console caller; `None` for an `executer` (no owner identity → every `private` resource denied). |

`is_executer` is derived from `role == "executer"`. `admin` is a console claim, never a caller/executer role.

## Change: Member

`members` table (feature 006) gains:

| Column | Type | Default | Notes |
|---|---|---|---|
| `teams` | `text[]` | `'{}'` NOT NULL | Server-authoritative team membership under RLS (FR-016). Administered via member create/update. |

`console_role` unchanged (already authoritative since 012).

## Change: Governed resource families (add `access_policy`)

`access_policy jsonb NOT NULL DEFAULT '{"visibility":"workspace"}'` added to:

| Table | Had it before? |
|---|---|
| `knowledge_bases` | ✅ yes (no change) |
| `agents` | ➕ added |
| `tools` | ➕ added |
| `mcp_servers` | ➕ added |
| `mcp_tools` | ➕ added |
| `skills` | ➕ added |

> `mcp_tools` inherit from their server operationally, but carry their own column for uniform enforcement; the
> default `workspace` means "governed by nothing extra" unless explicitly set.

## Migration: `0006_access_policy_and_member_teams`

- **upgrade**: `ALTER TABLE members ADD COLUMN teams text[] NOT NULL DEFAULT '{}';` and, for each of
  `agents, tools, mcp_servers, mcp_tools, skills`:
  `ADD COLUMN access_policy jsonb NOT NULL DEFAULT '{"visibility":"workspace"}';`
- **downgrade**: drop those columns.
- **Safety**: additive, DB-defaulted, no backfill; existing rows become ungoverned (`workspace`) → unchanged
  behavior (FR-001). RLS policies are unaffected (no new table, no policy change).
- **Verification**: migration test applies `upgrade head` on a clean DB and asserts the columns + defaults exist;
  the existing RLS/immutability integration tests must stay green.

## Schema contracts (Pydantic) that change

| Schema | Change |
|---|---|
| `schemas/members.py` `MemberOut` / member create+update | add `teams: list[str] = []` |
| `schemas/agents.py`, `tools.py`, `mcp.py`, `skills.py`, `knowledge.py` | create/patch accept optional `access_policy`; `*Out` surfaces `access_policy` |

> `access_policy` is validated/normalized through the evaluator's parse step before persistence; an invalid shape
> is rejected (`input.invalid`) rather than silently stored, so a malformed policy never becomes an open grant.

## Frontend contract (FR-017, mock/type level)

| File | Change |
|---|---|
| `src/lib/data.ts` | `Member` type + member create/update payloads gain `teams: string[]` (`@backend`) |
| `src/lib/iam.ts` / `auth.tsx` | document teams as server-owned for authZ; `MOCK_PROFILE.teams` = pre-wiring preview |
| members settings roster (`settings/page.tsx`) | **display-only** teams column (optional) — **NO edit UI** (roster stays read-only/IdP-managed per FR-017) |
| `openapi.json` | regenerated so `MemberOut`/member input include `teams` |

> The console's `Member` mock is claim-based (`groups`, role resolved via `memberRoleFor`) and the backend
> `MemberOut` is store-based (`subject`, `console_role`) — these already differ (part of the unwired 0/253 gap).
> This feature only adds `teams` to both sides' contracts; it does not reconcile the broader member-shape
> divergence (that is FE-wiring work, finding F-D).

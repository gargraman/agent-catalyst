# Phase 1 Data Model: 020 IdP Group → Members-Store Sync

Authoritative shapes are the shared umbrella: [015 data-model — `group_members` + `members` sync writer + `idp_binding`](../015-m5-security-completion/data-model.md). This file records the 020-specific **migration + ORM** decisions, including where on-disk reality diverges from the umbrella.

## Postgres — `workspaces.idp_binding` (NEW column — the umbrella is wrong)

The umbrella calls this "existing jsonb, consumed not changed." On disk the `Workspace` row has **only** `group_prefix varchar(64)`. **020 adds** `idp_binding jsonb NOT NULL DEFAULT '{}'` in migration `0010` (plan.md D1).

Shape (native refs — name/GUID/attr, no naming convention, FR-021):
```json
{
  "roles": { "viewer": "<ref>", "builder": "<ref>", "publisher": "<ref>" },
  "teams": { "SOC": "<ref>", "Detection": "<ref>" }
}
```
`<ref>` = an Okta group name, an Entra object-GUID, or a SAML attribute value. ORM: `Workspace.idp_binding: Mapped[dict]` (JSONB).

## Postgres — `group_members` (NEW table, `0010`) — notification fan-out projection

Per [015 data-model §`group_members`](../015-m5-security-completion/data-model.md). **Cache-only; MUST NOT be read to grant access** (FR-023).

| Field | Type | Notes |
|-------|------|-------|
| `workspace_id` | uuid | tenant key (D5 ruled — WorkspaceScoped under RLS) |
| `group_ref` | text | native IdP ref (matches `idp_binding` values) |
| `subject` | text | OIDC subject |
| `source` | text | `members_of` (scheduled enumeration) \| `login_seen` (upserted on login) |
| `seen_at` | timestamptz | freshness |
| pk | `(workspace_id, group_ref, subject)` | |

- **Validation:** cache only; may be stale; never an authorization read (FR-023). **WorkspaceScoped under RLS** mirroring `0005`/`0006` (D5 ruled — defense-in-depth, P5).

## Postgres — `members` (existing) — CHANGE: write path only

**No schema change.** Already has `UNIQUE (workspace_id, subject)` (`0005`), `console_role text default 'viewer'` (`0005`), `teams text[] default '{}'` (`0006`), RLS. The sync writer upserts `console_role`+`teams` via `INSERT ... ON CONFLICT (workspace_id, subject) DO UPDATE` under a `tenant_session`, **reconciling to the authoritative group set — including clearing a previously-synced role/team when groups shrink (D4 revoke)**; a `None` (can't-enumerate) result is a no-op and never revokes (D3). Reads (`membership.role_for`/`teams_for`) are the authority and are **unchanged** (FR-021).

## Adapter seam — CHANGE (optional capability, no persistence)

`IdPProvider` gains **optional** `groups_for(subject) -> list[str] | None` and `members_of(group_ref) -> list[str] | None` (capability-detected; `None` = unsupported). Existing providers (`dev`/`trellix-iam`/`jwt`) unchanged; `trellix-iam` returns `None` (claim-less — D3).

## No change

`api_tokens` (018), console session (019), egress config (017), the access-policy spine (013), `membership.py` reads — all untouched. 020 is a write-side sync + a projection cache.

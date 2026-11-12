# Phase 1 Data Model: 018 Caller-Token Plane + Deny-Set

Authoritative schema is the shared umbrella: [015 data-model — `api_tokens` + deny-set](../015-m5-security-completion/data-model.md). This file records the 018-specific **migration + ORM** decisions only.

## Postgres — `api_tokens` (new, `0008_api_tokens.py`)

Fields exactly per [015 data-model §`api_tokens`](../015-m5-security-completion/data-model.md): `id` pk, `workspace_id` fk, `agent_id` fk, `name`, `token_hash` (**unique**), `prefix`, `scopes` jsonb, `created_at`, `last_used_at?`, `expires_at?`, `revoked_at?`.

**018 deltas:**
- Migration file is **`0008_api_tokens.py`** (down_revision = `0007`), not `0007` as the umbrella text says.
- WorkspaceScoped **RLS** policy mirroring the `0006`/`0007` tenant-isolation pattern (tenant GUC); cross-tenant read/write must be denied (SC-008 family — covered by an RLS integration test mirroring `test_integration_rls`).
- CHECK: `expires_at IS NULL OR expires_at > created_at`.
- ORM: new `ApiToken` in `api/models.py`.
- **Secret handling:** raw secret is generated at mint, returned once, and only `sha256(secret)` + `prefix` persist. Never logged/exported (FR-011, SC-005).

## Redis — deny-set (transient)

Per [015 data-model Redis table](../015-m5-security-completion/data-model.md): `deny:sub:{subject}` / `deny:tok:{token_id}`, TTL ≥ 3600s (`AC_DENYSET_TTL_S`). Presence → reject (401) on both console and caller planes; **fail closed** when a configured Redis is unreachable; no-op allow when `AC_REDIS_URL` is unset.

## No change

`members`, `group_members`, `workspaces.idp_binding` are **not** touched by 018 (those belong to 020). Console session is 019. Egress config is 017.

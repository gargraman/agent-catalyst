# Phase 0 Research: 020 IdP Group → Members-Store Sync

Shared design lives in the umbrella: [015 research R-note](../015-m5-security-completion/research.md) (role-authority reconciliation: the sync is a group→members-store write, not a live resolver). This file records only the **code-grounded deltas** resolved while planning 020.

## Resolved decisions

- **D-NoIdpBinding — `workspaces.idp_binding` does NOT exist on disk.** The umbrella data-model calls it "existing jsonb (18 §3), consumed not changed." Verified false: the server `Workspace` model (`models.py:59`) has **only** `group_prefix varchar(64)` (migration `0001`); no `idp_binding` column exists in `api/` or `migrations/`. The jsonb binding lives **only in the frontend** (`iam.ts` `idpBinding()` derives it from `group_prefix`). → 020 **creates** `workspaces.idp_binding jsonb` (plan.md D1). This is the biggest divergence from the umbrella text.
- **D-NoEnumeration — the adapter seam has only `verify`.** `IdPProvider` (`auth/base.py:24`) exposes no `groups_for`/`members_of`. → 020 extends the seam with **optional, capability-detected** enumeration (plan.md D2); existing providers (`dev`/`trellix-iam`/`jwt`) are untouched.
- **D-ClaimlessPilot — the trellix-iam token carries no groups.** `jwt_provider.verify` extracts only `sub`/`scope`/`iss` and sets `workspace_claims={}` (confirmed in 012 + the 2026-11-11 clarification). → for the pilot IdP the login sync is a **no-op**; the mechanism is for group-exposing IdPs (Okta/Entra/generic-OIDC). This is the central tension the R-note resolves (plan.md D3).
- **D-Migration0010 — not 0008.** `0008_api_tokens` (018) and `0009_api_tokens_created_by` already exist; `group_members` + `idp_binding` land in **`0010`**.
- **D-MembersUpsertReady — no `members` schema change.** `members` already has `UNIQUE (workspace_id, subject)` (`0005`), `console_role` (`0005`), `teams text[]` (`0006`), and RLS. The sync upserts via `ON CONFLICT (workspace_id, subject)` under a `tenant_session` — reads (`role_for`/`teams_for`) unchanged.
- **D-NoScheduler — the refresh is a callable, not wired.** No arq/RQ worker exists on disk. The `members_of` scheduled refresh ships as `sync.refresh_group_projection(...)` with a unit test; the **login-driven upsert is the only wired path** for the pilot (plan.md D5).

## Owner decisions (all ruled, 2026-11-12)

All plan decisions are now ruled (2026-11-12):
- ✅ **D1** — add the `workspaces.idp_binding jsonb` column (IdP-agnostic native refs; not the `group_prefix` convention).
- ✅ **D3** — the claim-less pilot IdP (`groups_for → None`) is a no-op that **never revokes**; the sync is load-bearing only for group-exposing IdPs.
- ✅ **D4** — **revoke on reconcile**: an *authoritative* group set reconciles the member row, clearing a prior synced role/team when groups shrink; a `None` result never revokes.
- ✅ **D5** — `group_members` is **WorkspaceScoped under RLS**, pk `(workspace_id, group_ref, subject)` (supersedes the umbrella's global pk).
- Spec fix: 020 `spec.md` Key Entities + Assumptions corrected (`idp_binding` is **created in 0010**, not "existing/consumed"; migration is `0010`, not `0008`).

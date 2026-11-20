# Implementation Plan: M5 — IdP Group → Members-Store Sync + Notification Projection

**Branch**: `feat/backend-m0-foundation` (feature dir `020-m5-idp-group-sync`) | **Date**: 2026-11-12 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/020-m5-idp-group-sync/spec.md` · **Parent umbrella**: [`015-m5-security-completion`](../015-m5-security-completion/) (shared design — research R-note, contracts/console-auth.md, data-model `group_members`/`idp_binding`) · **Depends on**: 019 (the sync runs on the `/auth/callback` login hook)

## Summary

Make authorization and notification fan-out track **real org IdP group membership** by **syncing a user's role/team into the members store** (the authority under RLS) at login, plus a **best-effort `group_members` projection** for notification fan-out. Per the reconciliation clarification (research R-note), this is a **group → members-store sync** (a write path into `members`), **not** a live per-request group→role resolver — the existing `require_permission`/access-policy stack keeps reading the members store unchanged (`membership.role_for`/`teams_for`, `membership.py`).

**Key reuse / deltas found in code (grounding the shared 015 design against what's actually on disk — these are the load-bearing surprises):**
- **`workspaces.idp_binding` does NOT exist on disk.** The 015 data-model calls it an "existing jsonb (18 §3), consumed not changed" — but the server `Workspace` model (`models.py:59`) has **only** `group_prefix varchar(64)` (migration `0001`). There is no `idp_binding` column anywhere in `api/` or `migrations/`. The jsonb binding is modeled **only in the frontend** (`iam.ts` `idpBinding()` derives `ap-<prefix>-<role>` from `group_prefix` — the Okta convention). **020 must create the binding storage** (Decision D1) — it is not a consume-only feature.
- **The adapter seam has no group enumeration.** The `IdPProvider` Protocol (`auth/base.py:24`) exposes **only** `verify(request) -> Principal`. There is no `groups_for(subject)` and no `members_of(group_ref)`. **020 must extend the seam** (Decision D2) with optional, capability-detected enumeration — providers that can't enumerate return `None` and the projection falls back to login-observed membership.
- **The pilot `trellix-iam` token is claim-less.** Verified in 012 + the 2026-11-11 clarification (research R-note): the Trellix-IAM token carries **no AC role/group claims** (`jwt_provider.verify` extracts only `sub`/`scope`/`iss`; `Principal.workspace_claims={}`). So for the pilot IdP the login-time `groups_for` yields nothing and the sync is effectively a **no-op for trellix-iam**; the mechanism exists for IdPs that *do* expose groups (Okta/Entra/generic-OIDC). This is the central tension the R-note resolves — surfaced in D3.
- **Migration number is `0010`, not `0008`.** The umbrella data-model says "create `0008_group_members`", but `0008_api_tokens` (018) and `0009_api_tokens_created_by` already exist. `group_members` lands in **`0010`** (which also adds `workspaces.idp_binding` per D1).
- **`members` already supports the upsert.** `members` has `UNIQUE (workspace_id, subject)` (`0005` migration), `console_role text default 'viewer'`, `teams text[] default '{}'` (`0006`), and is WorkspaceScoped (RLS). The sync writes via `INSERT ... ON CONFLICT (workspace_id, subject) DO UPDATE` under a `tenant_session` per workspace — no schema change to `members`.
- **No scheduler infra exists yet.** The designed `worker` (arq/RQ) is not on disk. The scheduled `members_of` refresh is written as a **callable function** invokable by a future scheduler; the **login-driven upsert is the primary (and only wired) path** for the pilot (D5).

## Technical Context

**Language/Version**: Python 3.12 (`services/`)
**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Alembic, Pydantic v2 (all present). No new runtime dependency. The login hook is 019's `/auth/callback` (authlib).
**Storage**: Postgres — **new** `0010` migration adds `workspaces.idp_binding jsonb` (D1) **and** the `group_members` cache table (D5); `members` is written (upsert) but **not** altered. No Redis.
**Testing**: pytest async + the tenant test session (RLS); deterministic — maps name/GUID/attr binding cases, no-match fail-closed, and projection source provenance. No model/prompt behavior → no eval layer. No live IdP.
**Project Type**: Web service (`services/api`)
**Performance Goals**: The sync runs once per login (rare, interactive) — one `groups_for` adapter call + a small per-workspace upsert. The scheduled refresh (where supported) is a background job, off the request path.
**Constraints**: a no-mapping-match user gets **no** role/team (fail-closed default, FR-022); `group_members` **MUST NOT** be read to grant access (FR-023 — notification fan-out only); **no** live per-request group→role resolution path may exist (FR-021); the sync writes only under a tenant-scoped session (RLS, P5); no vendor group-naming convention may be assumed (name/GUID/attr, FR-021).
**Scale/Scope**: internal pilot; the sync writer + the adapter enumeration extension + the `idp_binding` storage + the `group_members` projection. For the pilot IdP (trellix-iam, claim-less) the login sync is a no-op — the capability is for Okta/Entra/generic-OIDC.

## Decisions requiring review (the load-bearing choices)

> Where the shared design and the code on disk disagree, or where fidelity has a real cost. Flagged for critical review before tasks/code.

**D1 — Create `workspaces.idp_binding jsonb`; the umbrella's "existing, consumed-only" is wrong. — ✅ RULED (owner, 2026-11-12): add the jsonb column.**
Add `idp_binding jsonb NOT NULL DEFAULT '{}'` to `workspaces` in migration `0010`. Shape: `{"roles": {"viewer": <ref>, "builder": <ref>, "publisher": <ref>}, "teams": {<team>: <ref>}}` where `<ref>` is a **native** IdP group reference (Okta name, Entra object-GUID, or SAML attribute value) — **no** `ap-<prefix>-<role>` assumption (FR-021). *Rationale:* FR-021 requires name/GUID/attr refs without a naming convention; the existing `group_prefix varchar(64)` can express **only** the Okta prefix sugar, not Entra GUIDs or SAML attrs. *Alternative rejected:* reuse `group_prefix` + the `ap-<prefix>-<role>` convention (the prefix-only limitation — **not taken**; owner chose the IdP-agnostic jsonb column).

**D2 — Extend the adapter seam with optional, capability-detected group enumeration.**
Add two **optional** methods to the provider contract: `groups_for(subject) -> list[str] | None` (the user's native group refs; `None` = "this IdP can't tell us at login") and `members_of(group_ref) -> list[str] | None` (enumeration for the scheduled refresh; `None` = "not supported"). Capability-detected via `hasattr`/`Protocol` so existing providers (`dev`, `trellix-iam`, `jwt`) need no change. *Rationale:* the seam has only `verify` today; the sync needs group data without forcing every provider to implement enumeration. *Alternative rejected:* make enumeration mandatory on `IdPProvider` — breaks `dev`/`trellix-iam`/`jwt` and the claim-less token has nothing to return anyway. *Confirm:* the optional-capability shape.

**D3 — For the claim-less pilot IdP the login sync is a no-op; that is intended, not a gap. — ✅ RULED (owner, 2026-11-12): confirmed.**
`trellix-iam` returns no groups (`groups_for → None`), so at login nothing is synced and `members` is managed by other means (direct roster writes, future admin UX). The sync becomes load-bearing only for an IdP that exposes groups. *Rationale:* research R-note — authority is the members store, reconciled with the claim-less token; a live group→role resolver was explicitly ruled out. **Critical interaction with D4 (revoke):** `None` means "can't enumerate" and is a **no-op that never revokes** — only an *authoritative* result (a provider that actually returns the user's current groups) triggers reconcile/revoke. So the claim-less pilot token never wipes a member's role.

**D4 — No live per-request group→role path; the authZ stack is unchanged. Revoke-on-reconcile. — ✅ RULED (owner, 2026-11-12): revoke.**
The sync is the **only** writer; `require_permission`/access-policy keep reading `members` via `membership.role_for`/`teams_for` (`membership.py`, `deps.py`) with **zero** changes. **Revoke semantics (ruled):** when the provider returns an **authoritative** group set (not `None`), the sync **reconciles the member row to match it — including revoking** a previously-granted role/team that no longer maps (shrinking groups downgrade/remove access). A first-time no-match still writes **nothing** (fail-closed default, FR-022). A provider returning `None` (can't enumerate — the claim-less pilot, D3) is a **no-op and never revokes**. *Implementation note:* reconcile = set `console_role`/`teams` to the mapped result; if the authoritative result maps to nothing, clear the synced role/team (do not delete the row — leave an empty-role member so the audit trail/roster projection survives, and `role_for` returns the fail-closed default).

**D5 — `group_members` is a cache-only, workspace-scoped (RLS) projection; the scheduled refresh is a function, not wired to a scheduler. — ✅ RULED (owner, 2026-11-12): workspace-scoped + RLS.**
`group_members(workspace_id, group_ref, subject, source, seen_at)` (pk `(workspace_id, group_ref, subject)`) is upserted with `source='login_seen'` on login and `source='members_of'` on a scheduled enumeration. **WorkspaceScoped under RLS**, mirroring `members`/`0005`/`0006` (defense-in-depth, P5) — the umbrella's global `(group_ref, subject)` pk is superseded. It is **never** read to grant access (FR-023). Because no worker/arq infra is on disk, the refresh ships as a callable (`sync.refresh_group_projection(...)`) with a unit test; actual scheduling is deferred to when the worker lands.

## Constitution Check

*GATE: pre-Phase 0 + re-checked post-Phase 1.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P1 outcome | ✅ | AuthZ + notification fan-out track real org membership across heterogeneous IdPs; measurable SC-007 (every name/GUID/attr case correct; no-match grants nothing). No LLM. |
| P2 scope/phases | ✅ | Bounded 015e slice. The scheduled refresh is stubbed to a callable (no worker infra yet) — explicitly deferred, not silently dropped (D5). |
| **P3 deterministic fail-closed** | ✅ | Mapping resolution is ordinary code; a no-match login grants nothing (FR-022); `group_members` can never grant access (FR-023). No model instruction enforces anything. |
| P4 evidence | ✅ (N/A judge) | No model/prompt behavior; deterministic TDD. |
| **P5 data/tenant** | ✅ | The sync writes `members` only under a tenant-scoped RLS session; `group_members` is **WorkspaceScoped under RLS** (D5 ruled) and carries no secrets. idp_binding holds native group refs (not secrets). |
| P6 reproducible | ✅ | API/data-layer only; `services/runtime/` untouched; no invoke/snapshot change. |
| P7 providers/cost | ✅ | No provider transport/cost change; the adapter extension is identity enumeration, not model transport. |
| P8 operate/visible | ✅ | A no-op sync for the claim-less pilot IdP is explicit (D3), not a silent success; projection rows are marked best-effort with `source`. |
| P9 test-first | ✅ | Red→Green→Refactor; tenant session + fake adapter; name/GUID/attr + no-match + provenance cases; no live IdP. |

**Gate: PASS** — the `idp_binding` column (D1) and the WorkspaceScoped `group_members` table (D5) are new schema justified in Complexity Tracking. All decisions (D1/D3/D4/D5) now owner-ruled.

## Design

### Components

- **`migrations/versions/0010_idp_binding_and_group_members.py`** (NEW): (a) `ALTER TABLE workspaces ADD COLUMN idp_binding jsonb NOT NULL DEFAULT '{}'` (D1); (b) `CREATE TABLE group_members (workspace_id uuid NOT NULL, group_ref text NOT NULL, subject text NOT NULL, source text NOT NULL, seen_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (workspace_id, group_ref, subject))` **+ WorkspaceScoped RLS** mirroring the `0005`/`0006` tenant pattern (D5).
- **`api/models.py`** CHANGE: add `idp_binding: Mapped[dict]` (JSONB) to `Workspace`; add the `GroupMember` ORM model on the `WorkspaceScoped` base (D5).
- **`api/auth/base.py`** CHANGE: extend the provider contract with **optional** `groups_for`/`members_of` (Protocol members or a capability mixin; `hasattr`-detected). Existing providers unchanged.
- **`api/sync.py`** (NEW): `sync_member_from_groups(session, workspace_id, subject, groups)` — resolve an **authoritative** `groups` list through `workspaces.idp_binding` (name/GUID/attr) → upsert `members.console_role`+`teams` via `ON CONFLICT (workspace_id, subject)`, **reconciling to match** (shrinking groups downgrade/clear the synced role/team — D4 revoke); a first-time no-match writes nothing (FR-022); `groups is None` (can't enumerate) → no-op, never revoke (D3). `refresh_group_projection(...)` — the scheduled `members_of` path (callable; D5). Upserts the WorkspaceScoped `group_members`.
- **`api/routers/auth.py`** CHANGE (019's file): in `/auth/callback`, after `issue_session`, call `groups_for(subject)` on the active provider (if capable) and run `sync_member_from_groups` per mapped workspace + upsert the `group_members` projection (`source='login_seen'`). Best-effort: a sync failure logs and does not break login (surface: or should it? — proposed best-effort, since authZ reads fail-closed anyway).
- **No change** to `membership.py`, `require_permission`, or the access-policy spine (D4) — the authZ read path is untouched.

### Pipeline position (login-time sync)

`/auth/callback` (019): validate → `issue_session` → **[020] `groups_for(subject)` → `sync_member_from_groups` per workspace (writes `members`) → upsert `group_members` projection** → 302 to app. The sync is a write-side effect of login; it is **not** on any per-request authZ path (FR-021).

## TDD mapping (acceptance → Red test → command)

Target file: `tests/test_idp_group_sync.py` (+ `tests/test_migration_0010.py` mirroring `test_migration_0006`).

| Acceptance / FR | Red test | Command |
|-----------------|----------|---------|
| FR-021, AS1 (name) | mapping role→group (name) + team→group; login with matching groups → `members.console_role`/`teams` upserted | `::test_sync_sets_role_team_from_name_mapping` |
| FR-021, AS4 (GUID/attr) | same with an Entra object-GUID ref and a SAML attribute-value ref — no name-prefix assumption | `::test_sync_resolves_guid_and_attr_refs` |
| FR-022, AS2 | login whose groups match no mapping → no role/team written (fail-closed) | `::test_no_match_grants_nothing` |
| FR-021 (no live path) | assert no per-request group→role resolver exists; authZ still reads `members` only | `::test_no_live_group_to_role_path` |
| FR-023, AS3 (login) | projection upserted `source='login_seen'` on login; never read to grant access | `::test_projection_login_seen_not_authz` |
| FR-023, AS3 (refresh) | `refresh_group_projection` via a `members_of`-capable fake adapter upserts `source='members_of'` | `::test_scheduled_refresh_enumerates` |
| D3 (claim-less) | `trellix-iam`-style provider (`groups_for→None`) → sync is a no-op, login still succeeds | `::test_claimless_provider_sync_is_noop` |
| D4 (revoke on reconcile) | an authoritative shrinking group set downgrades/clears the synced role/team; a `None` result never revokes | `::test_group_shrink_revokes_but_none_is_noop` |
| RLS (P5) | the sync writes only within the tenant session; cross-tenant upsert is invisible/denied | `::test_sync_is_workspace_scoped` |

Regression: full `pytest -q` (the 019 login path + the 012/013 authZ reads must stay green). Lint/type: `ruff check . && mypy .`.

## Project Structure

```text
services/
├── api/
│   ├── sync.py                  # NEW — sync_member_from_groups + refresh_group_projection (the only writers)
│   ├── auth/base.py             # CHANGE — optional groups_for/members_of on the provider contract
│   ├── routers/auth.py          # CHANGE (019's file) — call the sync on /auth/callback after issue_session
│   └── models.py                # CHANGE — Workspace.idp_binding (JSONB) + GroupMember ORM
├── migrations/versions/
│   └── 0010_idp_binding_and_group_members.py   # NEW — workspaces.idp_binding + group_members (NOT 0008)
└── tests/
    ├── test_idp_group_sync.py        # NEW (tenant session + fake enumerating adapter)
    └── test_migration_0010.py        # NEW (mirror test_migration_0006)
```

**Structure Decision**: the sync is a new, isolated write module (`sync.py`) invoked from 019's callback — it does **not** touch the authZ read path (`membership.py`/`require_permission`), honoring "no live group→role resolver" (FR-021) at the structural level. The two schema additions are forced by on-disk reality (D1: no `idp_binding`; D5: new projection table), not by the umbrella's (stale) "consume-only" framing. `services/runtime/` untouched (P6).

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
|-----------|-----------|------------------------------|
| New `workspaces.idp_binding jsonb` column (D1) | FR-021 needs name/GUID/attr native refs with no naming convention; `group_prefix` can only express the Okta prefix sugar | Reusing `group_prefix` + `ap-<prefix>-<role>` — hard-codes a vendor scheme, violating FR-021 / IdP-agnosticism (18 §1-3) |
| Extending the provider seam with `groups_for`/`members_of` (D2) | The seam has only `verify`; the sync needs group data per IdP capability | Mandatory enumeration on `IdPProvider` — breaks `dev`/`trellix-iam`/`jwt`, and the claim-less token has nothing to return |
| New `group_members` projection table (D5) | FR-023 needs a notification fan-out projection distinct from the authZ members store | Reusing `members` for fan-out — conflates a cache with the authZ source of record (the exact thing FR-023 forbids) |
| Shipping a sync inert for the pilot IdP (D3) | R-note: authority is the members store; a live group→role resolver was ruled out; trellix-iam is claim-less | A live per-request group→role resolver — explicitly rejected by the clarification and FR-021 |

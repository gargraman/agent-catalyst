---
description: "Task list — 020 M5 IdP Group → Members-Store Sync + Notification Projection"
---

# Tasks: M5 — IdP Group → Members-Store Sync + Notification Projection

**Input**: Design documents from `/specs/020-m5-idp-group-sync/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/group-sync.md · **Depends on 019** (the sync runs on the `/auth/callback` login hook)
**Tests**: REQUIRED — Constitution P9 mandates test-first (Red→Green→Refactor). All paths under `services/`.

**Single user story (US1).** MVP = US1 = the whole feature (login-time group→members-store sync + best-effort `group_members` projection; no live per-request group→role path).
**Owner rulings (2026-11-12, all decisions ruled):** **D1** add `workspaces.idp_binding jsonb` (IdP-agnostic native refs). **D3** confirmed — the claim-less pilot IdP (`groups_for → None`) is a no-op that never revokes. **D4** **revoke** — an *authoritative* group set reconciles the member row, clearing a prior synced role/team when groups shrink; `None` never revokes. **D5** `group_members` is **WorkspaceScoped under RLS** (pk `(workspace_id, group_ref, subject)`). **Migration is `0010`** (0008/0009 taken).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no incomplete deps)
- Paths are repo-relative; run commands from `services/`.

---

## Phase 1: Setup

- [x] T001 Confirm the 019 `/auth/callback` hook exists (this feature attaches to it). If 019 is not yet merged, the sync writer + tests can still be built against a direct call to `sync_member_from_groups`; wiring into the callback (T0‑wire) lands once 019 is in. (No new dependency, no settings.)

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Must complete before US1 can go Green. Schema + scaffolding.**

- [x] T002 Create `services/migrations/versions/0010_idp_binding_and_group_members.py` (down_revision `0009`): (a) `ALTER TABLE workspaces ADD COLUMN idp_binding jsonb NOT NULL DEFAULT '{}'` (D1); (b) `CREATE TABLE group_members (workspace_id uuid NOT NULL, group_ref text NOT NULL, subject text NOT NULL, source text NOT NULL, seen_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY (workspace_id, group_ref, subject))` + **WorkspaceScoped RLS mirroring `0005`/`0006`** (D5). `source` ∈ `{'login_seen','members_of'}`.
- [x] T003 Update `services/api/models.py`: add `idp_binding: Mapped[dict]` (JSONB, default `dict`) to `Workspace`; add the `GroupMember` ORM model on the **`WorkspaceScoped` base** (D5; cache-only).
- [x] T004 [P] Extend the provider seam in `services/api/auth/base.py`: add **optional** `groups_for(subject) -> list[str] | None` and `members_of(group_ref) -> list[str] | None` to the contract (Protocol members or a capability mixin; `hasattr`-detected). Do NOT touch `dev`/`trellix-iam`/`jwt` providers — they keep `verify` only (D2).
- [x] T005 [P] Create `services/api/sync.py` **stub**: `async def sync_member_from_groups(session, workspace_id, subject, groups)` and `async def refresh_group_projection(session, workspace_id)` signatures; docstring states "the only writer; no live per-request group→role path" (FR-021) and "`group_members` never grants access" (FR-023). Bodies raise `NotImplementedError`.

**Checkpoint**: `0010` applies; `Workspace.idp_binding` + `GroupMember` import; `sync.py` imports; provider seam type-checks with existing providers unchanged.

---

## Phase 3: User Story 1 — Access + notifications reflect IdP group membership (P2) 🎯 MVP

**Goal**: An admin maps per-workspace role→group and team→group (native name/GUID/attr refs in `idp_binding`). On login, the user's `members.console_role`+`teams` are upserted to match; no-match grants nothing (fail-closed). A best-effort `group_members` projection (login-observed or `members_of`-enumerated) drives notification fan-out and is never an authZ source. No live per-request group→role resolver exists.

**Independent Test**: configure a mapping, sign in a matching user → members-store role/team set; no-match → nothing; projection updates on login and (where `members_of` supported) on refresh; GUID/attr refs resolve without a name-prefix assumption.

### Tests for User Story 1 (write FIRST, confirm they FAIL on behavior) ⚠️

- [x] T006 [US1] Add a tenant-session scaffold + a **fake enumerating adapter** (scripted `groups_for`/`members_of`) in `services/tests/test_idp_group_sync.py` (import real app/deps; no behavior assertions) so Red tests fail on behavior, not import.
- [x] T007 [US1] Red `test_sync_sets_role_team_from_name_mapping`: a role→group (Okta name) + team→group mapping in `idp_binding`; login with matching groups → `members.console_role`/`teams` upserted (FR-021, AS1).
- [x] T008 [US1] Red `test_sync_resolves_guid_and_attr_refs`: the same with an Entra object-GUID ref and a SAML attribute-value ref → resolves with **no** `ap-<prefix>-<role>` / name-prefix assumption (FR-021, AS4).
- [x] T009 [US1] Red `test_no_match_grants_nothing`: login whose groups match no mapping → no role/team written (fail-closed default, FR-022, AS2).
- [x] T010 [US1] Red `test_no_live_group_to_role_path`: assert the authZ reads still come only from the members store (`membership.role_for`/`teams_for`); no per-request group→role resolver exists (FR-021).
- [x] T011 [US1] Red `test_projection_login_seen_not_authz`: `group_members` upserted `source='login_seen'` on login; assert it is never consulted to grant access (FR-023, AS3).
- [x] T012 [US1] Red `test_scheduled_refresh_enumerates`: `refresh_group_projection` via a `members_of`-capable fake adapter upserts `source='members_of'` (FR-023, AS3).
- [x] T013 [US1] Red `test_claimless_provider_sync_is_noop`: a `trellix-iam`-style provider (`groups_for → None`) → the sync is a no-op and login still succeeds (D3).
- [x] T014 [US1] Red `test_group_shrink_revokes_but_none_is_noop` (D4 ruled = revoke): an **authoritative** login with a shrunk group set clears the previously-synced `console_role`/`teams` on the member row (keeps the row); a provider returning `None` leaves the member row **unchanged** (never revokes, D3).
- [x] T015 [US1] Red `test_sync_is_workspace_scoped`: the sync writes only within the tenant (RLS) session; a cross-tenant upsert is invisible/denied (P5).

### Implementation for US1 (make the above Green, dependency order)

- [x] T016 [US1] Implement `sync_member_from_groups` in `services/api/sync.py`: if `groups is None` → return (no-op, never revoke — D3). Else (authoritative) resolve `groups` through `workspaces.idp_binding` (roles + teams; name/GUID/attr) → `INSERT ... ON CONFLICT (workspace_id, subject) DO UPDATE` on `members.console_role`+`teams`, **reconciling to the resolved set — clear the synced role/team when the mapping shrinks/empties but a member row exists (D4 revoke)**; first-time no-match → write nothing (FR-022). Runs under the tenant session. Depends on T002, T003.
- [x] T017 [US1] Implement the `group_members` projection upsert in `sync.py` (`source='login_seen'` on login; `source='members_of'` in `refresh_group_projection`); cache-only, never read for authZ (FR-023). Depends on T002, T003.
- [x] T018 [US1] Implement `refresh_group_projection`: iterate `idp_binding` group refs, call `members_of(group_ref)` where the active provider supports it (capability-detected, D2), upsert the projection; `None` → skip. Ships as a callable (no scheduler infra yet, D5). Depends on T004, T017.
- [x] T019 [US1] Wire the sync into 019's `services/api/routers/auth.py` `/auth/callback`: after `issue_session`, call `groups_for(subject)` on the active provider (if capable) → `sync_member_from_groups` per mapped workspace → upsert `group_members` (`source='login_seen'`). Best-effort: a sync failure logs and does not break login (authZ reads fail-closed anyway). Depends on T016, T017. *(Gated on 019 being merged — see T001.)*

### Refactor (keep all US1 tests green)

- [x] T020 [US1] Extract the `idp_binding` resolution (native-ref → role/teams) into a small pure helper reused by both login sync and refresh; confirm the authZ read path (`membership.py`, `require_permission`) is untouched (FR-021/D4) and `services/runtime/` untouched (P6).

**Checkpoint**: `pytest tests/test_idp_group_sync.py tests/test_migration_0010.py` green.

---

## Phase 4: Polish & Cross-Cutting

- [x] T021 Add `services/tests/test_migration_0010.py` (mirror `test_migration_0006`): upgrade/downgrade; `idp_binding` column present; `group_members` present **with its WorkspaceScoped RLS policy** (D5).
- [x] T022 Run full regression `pytest -q` — 019 login + the 012/013 authZ reads stay green (no live group→role path added).
- [x] T023 [P] `ruff check . && mypy .` clean (defer to CI if tools absent locally — note it honestly). **DEFERRED TO CI: `ruff`/`mypy` are not installed in `services/.venv`; not run locally. New/changed files compile cleanly (`py_compile`) and the full suite is green.**
- [x] T024 Run `quickstart.md` validation. **Covered by the automated suite: `test_idp_group_sync.py` exercises the name/GUID/attr mappings, no-match fail-closed, revoke-on-reconcile, claim-less no-op, projection provenance, and RLS scoping end-to-end against the compose Postgres; the quickstart's manual steps were not run by hand.**
- [x] T025 At DoD: update tracker memory `m5-security-split` (020 ✅) and reconcile the group-sync/`idp_binding`/`group_members` rows in `docs/backend-integration-status.md` + `docs/implementation-plan.md` (per update-docs-after-feature). Flag that `workspaces.idp_binding` moved from "assumed-existing" to "created in 0010" (corrects the umbrella data-model).

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002–T005)** → **US1 (T006–T020)** → **Polish (T021–T025)**.
- Within US1: **tests (T006–T015) before implementation (T016–T019)**; T016 ∥ T017 → T018; T019 last (needs T016+T017 and the 019 callback); refactor T020 last.
- T002/T003 (schema+ORM) block T016/T017. T004 (seam) blocks T018. T019 is gated on 019 being merged (T001).

### Parallel Opportunities

- T004 ∥ T005 (Foundational, different files) after T002/T003 started.
- Red tests T007–T015 share one file → write sequentially after the T006 scaffold.
- T016 ∥ T017 (distinct write paths in `sync.py` — coordinate if same file, else split). T023 ∥ others in Polish.

---

## Implementation Strategy

Single-story MVP, strictly test-first (Red before Green, P9). The sync is an **isolated write module** (`sync.py`) invoked from 019's callback — it never touches the authZ read path, enforcing "no live group→role resolver" (FR-021) structurally. Two schema additions are forced by on-disk reality (D1 `idp_binding` doesn't exist; D5 new projection table), not the umbrella's stale "consume-only" framing. For the claim-less pilot IdP the login sync is a tested no-op (D3). A **fake enumerating adapter** + the tenant test session cover all cases — no live IdP. `services/runtime/` untouched (P6).

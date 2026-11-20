# Quickstart / Validation: 020 IdP Group → Members-Store Sync

Contract: [group-sync.md](./contracts/group-sync.md) (+ shared [015 contracts/console-auth.md](../015-m5-security-completion/contracts/console-auth.md)). Run from `services/` with the venv active.

## Setup

```bash
cd services && source .venv/bin/activate
alembic upgrade head          # applies 0010 (workspaces.idp_binding + group_members)
pytest -q tests/test_idp_group_sync.py tests/test_migration_0010.py
```

Tests use the tenant test session (RLS) and a **fake enumerating adapter** (returns scripted groups / `members_of`) — deterministic, **no live IdP**.

## Validation scenarios (prove the feature)

1. **Name mapping** — configure `idp_binding` role→group (Okta name) + team→group; login a user with matching groups → `members.console_role`/`teams` upserted.
2. **GUID / attr mapping** — same with an Entra object-GUID ref and a SAML attribute-value ref → resolves with **no** name-prefix assumption.
3. **No match → nothing** — login whose groups match no mapping → no role/team written (fail-closed, FR-022).
4. **No live path** — assert there is no per-request group→role resolver; authZ still reads `members` only (FR-021).
5. **Projection (login)** — `group_members` upserted `source='login_seen'`; never consulted for access.
6. **Projection (refresh)** — `sync.refresh_group_projection` via a `members_of`-capable fake adapter upserts `source='members_of'`.
7. **Claim-less IdP** — a `trellix-iam`-style provider (`groups_for → None`) → sync is a no-op; login still succeeds (D3).
8. **Workspace-scoped** — the sync writes only within the tenant session; a cross-tenant upsert is invisible/denied (RLS).

## Done-when

- All TDD-mapping tests (plan.md) green; full `pytest -q` green (019 login + 012/013 authZ reads unchanged).
- `ruff check . && mypy .` clean.
- `0010` migration test green (mirrors `test_migration_0006`); RLS isolation test for `group_members` if D5-scoped.
- `docs/backend-integration-status.md` 020 rows flipped to BUILT (per the milestone done-when).

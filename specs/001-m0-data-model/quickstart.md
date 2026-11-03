# Quickstart — Verify M0 Data-Model Completion

Run/validation guide. Implementation details live in `tasks.md` + the code; this proves the feature works end-to-end.

## Prerequisites
- Dev stack up: `docker compose -f deploy/compose.yaml up -d` (Postgres on :5433, healthy).
- `cd services && source .venv/bin/activate` (test deps already installed).
- Schema migrated to head **including the new `0004`**: `alembic upgrade head`.

## 1. Migrations apply cleanly (and roll back)
```bash
alembic upgrade head      # 0004 creates knowledge_bases + 3 binding tables + enums + RLS
alembic downgrade -1      # 0004 reverses cleanly
alembic upgrade head
```
Expected: `alembic current` → `0004 (head)`; `\dt` shows `knowledge_bases, retrieval_bindings, skill_bindings, tool_bindings` owned by `ac`.

## 2. Full test suite green (prior 117 + new)
```bash
pytest -q                      # unit (infra-free)
pytest -q -m integration       # real DB (RLS/immutability/tenancy/snapshot)
```
Expected: 0 failures. New modules: `test_orm_models.py`, `test_session_tenancy.py`, `test_to_snapshot.py`.

## 3. Tenant isolation is real (US1 / SC-001, SC-002)
Covered by `test_session_tenancy.py` (as `ac_app`):
- Insert an agent under workspace A; a `tenant_session(B)` lists/updates/deletes → A's row invisible / 0 rows affected.
- A session with workspace scope unset/empty → every tenant table returns 0 rows (fail-closed).
- Verify the connection role is `ac_app` (not a BYPASSRLS role).

## 4. Snapshot is frozen, reproducible, decoupled (US2 / SC-003, SC-004)
Covered by `test_to_snapshot.py`:
- `to_snapshot()` carries every behavior/authority/cost field (data-model.md) and `snapshot_hash == compute_snapshot_hash(av) == stored hash`.
- Clone a version (new ids, same content) → identical `snapshot_hash`.
- Mutate only `kb_index_version` → `snapshot_hash` unchanged.
- Snapshot reflects frozen columns, never the live `agents` row.

## 5. Immutability surfaced (SC-006)
Covered by `test_orm_models.py` (or the existing immutability integration test extended): updating a frozen `agent_versions` column raises (from the `0002` trigger), surfaced by the ORM — never a silent success.

## 6. Parity with the baseline
The new real-row `to_snapshot` hash matches `compute_snapshot_hash` run on the equivalent faked `av` from `conftest.py` — proving the ORM shape and the existing hashing authority agree.

## Done / DoD
- [ ] `0004` up/down clean; `alembic current` = `0004`.
- [ ] `pytest -q` and `pytest -q -m integration` both 0 failures (prior 117 still green).
- [ ] Negative cross-tenant + unset-scope tests pass as `ac_app`.
- [ ] `to_snapshot` clone-stable, `kb_index_version`-excluded, frozen-not-live.
- [ ] `runtime/` imports no DB/ORM (import check).
- [ ] Docs reconciled: note the `agent_versions` drift (research.md D5) in the completion report + flag the follow-up migration (per the "update docs after feature" agreement).

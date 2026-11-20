# Research — M0 Data-Model Completion (Phase 0)

All decisions are grounded in `docs/backend/01-data-model.md` (authoritative DDL + ORM pattern) and `docs/backend/00-overview.md §3` (module layout). No open `NEEDS CLARIFICATION` remain.

## D1 — Module homes (follow the design doc)

**Decision**: ORM + Alembic target → `services/api/models.py`; workspace-scope/RLS helper → `services/api/tenancy.py`; engine + session factory → `services/api/db.py`; the pure `VersionSnapshot` dataclass → `services/runtime/snapshot.py`.
**Rationale**: `00-overview §3` prescribes exactly this (`api/models.py` "SQLAlchemy ORM + Alembic target (01)", `api/tenancy.py` "workspace-scope guard + RLS session var (09)"). The constitution's source hierarchy makes the doc authoritative. `VersionSnapshot` lives in `runtime/` because the runtime consumes it and must import *only* the dataclass, never the ORM (`01` §4.4, §5).
**Alternatives rejected**: a separate `services/db/` package (my initial draft) — contradicts the doc; unnecessary since `api` owning persistence is the documented design and worker→`api.models` is a plain import, not an HTTP coupling.

## D2 — Tenant GUC mechanism

**Decision**: On the app path, connect as the non-superuser `ac_app` role and, at the start of each session's transaction, execute `select set_config('app.workspace_id', :ws, true)` (the `true` = `is_local`, scoped to the transaction). Expose this as a `tenant_session(ws_id)` async context manager (in `api/db.py`, using the `api/tenancy.py` helper for the exact SQL).
**Rationale**: matches the existing `conftest.py` integration helper (`set_config('app.workspace_id', :w, true)`) and `0002`'s RLS policy (`NULLIF(current_setting('app.workspace_id', true), '')::uuid`), which fails closed (unset/empty/invalid → NULL → zero rows). `is_local=true` confines the scope to the transaction so a pooled connection (PgBouncer txn-mode, per `09 §7`) never leaks scope between requests.
**Alternatives rejected**: `SET LOCAL app.workspace_id = '<uuid>'` string-interpolated — injection risk and no clean parameter binding; `set_config(..., false)` (session-level) — leaks across pooled checkouts.

## D3 — Reserved-name collision (`model_config`)

**Decision**: map the column `model_config` to ORM attribute **`model_config_`**: `model_config_: Mapped[dict] = mapped_column("model_config", JSONB)`.
**Rationale**: `model_config` is reserved by Pydantic v2 and collides with SQLAlchemy declarative internals; `01-data-model.md:895` and the existing `conftest.py`/`hashing.py` (`av.model_config_`) already use the trailing-underscore attribute. Consistency keeps `compute_snapshot_hash` unchanged.

## D4 — Two engines / two roles

**Decision**: migrations run as superuser `ac` via the **sync** `psycopg` URL (`SYNC_URL`, Alembic only); the application path uses a **new async `ac_app` engine** (asyncpg). Add an `ac_app` async URL to `api/db.py` (default `postgresql+asyncpg://ac_app:ac_app_pw@localhost:5433/agent_catalyst`, matching `conftest.AC_DB_URL`); real creds via Vault later.
**Rationale**: `0003` created `ac_app` with no BYPASSRLS specifically so the app path's RLS applies; the owner/migration role bypasses RLS (`09 §7`). The existing `api/db.py` `ASYNC_URL` defaults to `ac` — correct for tooling, wrong for the request path, so a distinct `ac_app` async engine is required.

## D5 — `agent_versions` schema drift (FLAG, do not fix here)

**Finding**: `01-data-model.md` defines `agent_versions` with `status` (`version_status` enum), `created_by`, `note`, and `unique(agent_id, label)` — **none of which migration `0001` created**. The frontend `Version` type also carries `status/createdBy/note`.
**Decision**: model the ORM to the **current** (`0001`) schema; **do not** add these columns in this feature. None are read by `compute_snapshot_hash`, so they are not needed for `to_snapshot()` or the snapshot-complete scope.
**Follow-up (flagged)**: a later migration should reconcile `agent_versions` to the doc (adds `status/created_by/note/unique` and updates the `0002` immutability trigger to treat `status` as mutable). Recorded here and surfaced in the completion report (per the "update docs after feature" working agreement). Owner decides when to schedule it.

## D6 — Binding-table immutability (defer triggers; add RLS now)

**Decision**: migration `0004` adds RLS policies to the new binding/KB tables (P5 — required) but does **not** add per-row immutability triggers to the binding tables in this feature.
**Rationale**: bindings are written once at version creation; any behavior-affecting change is already covered because it changes `agent_versions.snapshot_hash` (which `0002` freezes). So binding edits are detectable/version-busting without a per-table trigger. Adding 3 more triggers is deferred to keep the feature bounded.
**Follow-up (flagged)**: consider binding-table immutability triggers when the version-create path is built (M4), for defense-in-depth.

## D7 — Scope of new tables (snapshot-complete minimal)

**Decision**: `0004` creates exactly: `knowledge_bases` (full, per doc — needed for `retrieval_binding.kb.embedding_model`), `retrieval_bindings`, `skill_bindings`, `tool_bindings`, plus the enums `tool_kind` and `tool_risk_tier` (used by `tool_bindings`).
**Excluded** (deferred): `data_sources`, `documents`, `chunks` (ingestion → M7 RAG; not read by the snapshot), and the registry tables `skills`, `tools`, `mcp_servers`, `mcp_tools`, `subagent_configs` (M4+). Consequently the binding `*_id` columns that would FK to registries (`tool_id`, `mcp_tool_id`, `subagent_config_id`, `skill_id`) are **plain nullable uuids without FK constraints** until those registries land; `retrieval_bindings.kb_id` and all `version_id`/`workspace_id` FKs are real (their targets exist).
**Rationale**: the owner chose "snapshot-complete minimal"; the snapshot reads only self-contained binding columns (`schema_snapshot`, `config_snapshot`, `skill_content_hash`, `embedding_model`, etc.) plus identity keys for sort-stability, so registry tables are not required for `to_snapshot()`.
**No pgvector dependency** in this feature: `knowledge_bases` has no `vector` column (the `chunks` embedding column is deferred with ingestion).

## D8 — Testing strategy

**Decision**: three new test modules, mirroring the existing split (unit where possible, `@pytest.mark.integration` against the real DB which skips when unreachable). Negative cross-tenant (read/update/delete) + unset-scope fail-closed tests connect as `ac_app` (superuser bypasses RLS and would give false greens). `to_snapshot` tests assert clone-stable hash and `kb_index_version` exclusion using real rows, then cross-check against `compute_snapshot_hash` on an equivalent faked `av` to prove parity with the existing 117-test baseline.
**Rationale**: constitution P9 + the Minimum-Verification-Checks row for DB/tenant behavior (negative cross-tenant cases) and model/reproducibility.

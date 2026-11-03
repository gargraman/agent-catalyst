# Internal Interface Contracts — M0 Data-Model Completion

This feature exposes no external/HTTP interfaces. It defines three internal contracts other backend code depends on. Signatures are indicative (final types settled during TDD); behavior is binding.

## C1 — Tenant-scoped session factory (`services/api/db.py`)

```python
@asynccontextmanager
async def tenant_session(workspace_id: str | UUID) -> AsyncIterator[AsyncSession]:
    """Yield an AsyncSession bound (as ac_app) to exactly one workspace for one transaction."""
```

**Contract**:
- Connects via the **`ac_app`** async engine (never the superuser/owner role).
- On entering the transaction, sets `app.workspace_id` to `workspace_id` via `set_config(..., is_local=true)` (see `api/tenancy.py`).
- Every read/write through the yielded session is RLS-filtered to `workspace_id`.
- An empty/invalid/missing `workspace_id` MUST result in zero rows for tenant tables (fail-closed), never an error or all-rows.
- Scope does not leak to the next use of a pooled connection.

Companion helper (`services/api/tenancy.py`):
```python
async def set_workspace_scope(session: AsyncSession, workspace_id) -> None:
    """Execute set_config('app.workspace_id', :ws, true) on the session's transaction."""
```

## C2 — `AgentVersion.to_snapshot()` (`services/api/models.py`)

```python
def to_snapshot(self) -> "VersionSnapshot":  # VersionSnapshot from services/runtime/snapshot.py
    """Materialize the frozen, runtime-ready snapshot. Pure read; no live agent row."""
```

**Contract**:
- Assembles a `VersionSnapshot` from this frozen `AgentVersion` + its loaded `retrieval_bindings`, `tool_bindings`, `skill_bindings`.
- `snapshot.snapshot_hash` MUST equal `compute_snapshot_hash(self)` (reused from `runtime/hashing.py`) and the stored `agent_versions.snapshot_hash`.
- MUST be clone-stable (two versions differing only by row ids → identical hash) and MUST exclude `kb_index_version` from the hash.
- MUST NOT read the live `agents` row.
- Bindings must be eager-loaded (or loaded within the session) before calling, so no lazy-load I/O escapes the snapshot assembly.

## C3 — `VersionSnapshot` (`services/runtime/snapshot.py`)

```python
@dataclass(frozen=True)
class VersionSnapshot: ...   # fields per data-model.md
```

**Contract**:
- Pure value type; `services/runtime/*` imports it and nothing DB/ORM/HTTP-related.
- Immutable (`frozen=True`); all nested binding collections are tuples, not lists.
- Is the single input type the runtime's `invoke(snapshot, inputs, ctx)` (M2) will accept.

## Consumers (forward references, not built here)
- **M2 `runtime.invoke`** reads `VersionSnapshot`.
- **M4 API / worker** open `tenant_session(ws)` per request/job and call `to_snapshot()` to hand the runtime a frozen snapshot.

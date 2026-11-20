# Data Model — Feature 025 Platform Hardening (Phase 1)

Two halves: **runtime value objects** (pure, `runtime/`) for provider resilience, and **schema changes** (migration
`0014_runs_retry_count` + `0015_binding_integrity`) for binding integrity. Decisions referenced as D# / G# are in [research.md](research.md).

## A. Runtime value objects (pure — no redis/prometheus/FastAPI imports)

### A1. `ResiliencePolicy` (frozen dataclass, `runtime/providers/resilience.py`)
| Field | Type | Default | Rule |
|-------|------|---------|------|
| `max_attempts` | int | 3 | total attempts incl. the first; `≥ 1`; `1` disables retry |
| `backoff_base_s` | float | 0.5 | wait before attempt n (n≥2) = `uniform(0, base × 2^(n-2))` (full jitter) |
| `per_wait_cap_s` | float | 8.0 | every individual wait `≤` this, Retry-After included (F11) |
| `min_attempt_budget_s` | float | 0.5 | do not start an attempt with less remaining deadline than this |
| `failure_threshold` | int | 5 | consecutive transient failures that open a breaker |
| `cooldown_s` | float | 30.0 | open → half-open after this (TTL of the `open` marker) |
| `batch_concurrency` | int | 4 | per-process in-flight cap per (provider, model) for class `batch` |

Validation: all numerics `> 0` (`max_attempts ≥ 1`); built from `AC_PROVIDER_*` settings in the API (see contracts).

### A2. `TrafficClass` = `Literal["live", "batch"]`
Derived by `traffic_class(source)`: `eval → batch`; `playground | api | channel | tool_test | subagent → live`;
unknown source → `live` (never silently batch-throttle a user). Ingestion embedding calls pass `batch` explicitly.

### A3. `BreakerKey` (frozen) — `provider: str, model: str, cls: TrafficClass`
No workspace, agent, or user id — breaker state contains no tenant data (spec Key Entities).

### A4. Breaker state machine (per `BreakerKey`)
```
            transient failure (count < N)
          ┌──────────────┐
          ▼              │
     ┌─────────┐  N consecutive  ┌────────┐  cooldown elapsed  ┌───────────┐
     │ CLOSED  │───transient────▶│  OPEN  │──(marker expires)─▶│ HALF-OPEN │
     └─────────┘    failures     └────────┘                    └───────────┘
          ▲                          ▲   refuse all calls        │  exactly one trial
          │ success (DEL fails)      │                           │  (SET trial NX);
          │                          └──── trial fails ──────────┤  others refused
          └───────────────────────────── trial succeeds ─────────┘
```
Store representation (D5): `fails` counter, `open` marker with TTL = cooldown, `trial` NX lease with TTL =
deadline + 5 s. Live-open also refuses batch (FR-011); batch-open never refuses live.

### A5. `BreakerStore` (Protocol) — implementations `LocalBreakerStore` (runtime) and `RedisBreakerStore` (api)
| Method | Returns | Semantics |
|--------|---------|-----------|
| `admit(key, *, also_check: BreakerKey \| None, trial_ttl_s)` | `Admission` (`"closed" \| "trial" \| "refused"`) | refused if `open` (own or `also_check`), trial if half-open and lease won |
| `record_success(key, *, was_trial)` | None | close: clear `fails` + `trial` |
| `record_failure(key, *, was_trial)` | `opened: bool` | atomic INCR; open on threshold or failed trial |

Any exception from a store method ⇒ guard falls back to its `LocalBreakerStore` for that call (D8).

### A6. `Guard` (runtime) — composed per process, carried on `RunContext.guard`
Holds `policy`, `store`, `local_store`, `observer`, `clock`, `sleep`, `rng`, and a lazy per-(provider, model)
`asyncio.Semaphore` map for batch. Used by `gateway.stream` / `gateway.embed`; never by callers directly.

### A7. Changes to existing runtime types
| Type | Change | Compatibility |
|------|--------|---------------|
| `TransportError` | `+ retry_after: float \| None = None` | additive kwarg |
| `RunContext` | `+ guard: Guard \| None = None` | `None` ⇒ process-default guard (retry + local breaker) |
| `Outcome` | `+ PROVIDER_UNAVAILABLE = "provider_unavailable"` | new member; free-text column (G10) |
| `RunRecord` | `+ retries: int = 0` | additive |
| `EmbeddingError` | new `type="provider_unavailable"` value; `+ attempts: int = 1` | additive |
| gateway USAGE / ERROR event `data` | `+ "attempts": int` | additive dict key |
| ERROR payload `type` | `+ "provider_unavailable"` | consumers map unknown types to provider_error today |

`RunRecord.retries = Σ over model turns (attempts − 1) + Σ over embedding calls (attempts − 1)`.

## B. Schema changes — migrations `0014_runs_retry_count` (B5 only) + `0015_binding_integrity` (B1–B4, B6)

### B1. FK target uniques
| Table | New constraint |
|-------|----------------|
| `skills` | `uq_skills_ws_id UNIQUE (workspace_id, id)` |
| `tools` | `uq_tools_ws_id UNIQUE (workspace_id, id)` |
| `mcp_tools` | `uq_mcp_tools_ws_id UNIQUE (workspace_id, id)` |
| `knowledge_bases` | `uq_kb_ws_id UNIQUE (workspace_id, id)` |
| `agent_versions` | `uq_agent_versions_id_agent UNIQUE (id, agent_id)` |

### B2. Reference FKs (all `ON DELETE RESTRICT`, `MATCH SIMPLE`)
| Constraint | From | To | Rule |
|------------|------|----|------|
| `fk_skill_bindings_skill` | `skill_bindings (workspace_id, skill_id)` | `skills (workspace_id, id)` | FR-016/017 |
| `fk_tool_bindings_tool` | `tool_bindings (workspace_id, tool_id)` | `tools (workspace_id, id)` | FR-016/017 (NULL skipped) |
| `fk_tool_bindings_mcp_tool` | `tool_bindings (workspace_id, mcp_tool_id)` | `mcp_tools (workspace_id, id)` | FR-016/017 (NULL skipped) |
| `fk_retrieval_bindings_kb_ws` | `retrieval_bindings (workspace_id, kb_id)` | `knowledge_bases (workspace_id, id)` | FR-016 same-workspace (existing single-col FK kept) |
| `fk_kb_chunks_kb` | `kb_chunks (workspace_id, kb_id)` | `knowledge_bases (workspace_id, id)` | G9 orphan guard |
| `fk_agents_active_version` | `agents (active_version_id, id)` | `agent_versions (id, agent_id)` | FR-020; **DEFERRABLE INITIALLY DEFERRED**; NULL active skipped |

`subagent_config_id` stays unconstrained (no catalog — spec Assumption).

### B3. CHECK
`ck_tool_bindings_one_target` on `tool_bindings` (FR-019):
- `kind IN ('http','builtin')` ⇒ `tool_id NOT NULL AND mcp_tool_id IS NULL AND subagent_config_id IS NULL`
- `kind = 'mcp'` ⇒ `mcp_tool_id NOT NULL AND tool_id IS NULL AND subagent_config_id IS NULL`
- `kind = 'subagent'` ⇒ `subagent_config_id NOT NULL AND tool_id IS NULL AND mcp_tool_id IS NULL`

### B4. Triggers
| Trigger | Table(s) | Fires | Rejects | SQLSTATE / prefix |
|---------|----------|-------|---------|-------------------|
| `bindings_freeze` | `skill_bindings`, `tool_bindings`, `retrieval_bindings` | BEFORE UPDATE OR DELETE, row | always (F15 parity, FR-018) | `23001` `frozen_binding:` |
| `bindings_insert_window` | same three | BEFORE INSERT, row | unless parent `agent_versions.xmin = pg_current_xact_id()::xid` | `23001` `frozen_binding:` |
| `kb_chunks_dim_check` | `kb_chunks` | BEFORE INSERT OR UPDATE OF embedding, kb_id | `vector_dims(NEW.embedding) <> knowledge_bases.embedding_dim` | `23514` `chunk_dim_mismatch:` |

Notes: `TRUNCATE` does not fire row triggers (test teardown unaffected). Deleting a version's bindings is therefore
impossible by design; versions themselves have no delete path today (verified). If a future "delete agent" needs it,
it must be an explicit, audited migration-level decision — not a silent trigger bypass.

### B5. `runs`
`retry_count integer NOT NULL DEFAULT 0` (FR-006). `outcome` remains free text; new value `provider_unavailable`.

### B6. Preflight (FR-022) — runs first inside `upgrade()`, with `SET LOCAL row_security = off`
| Rule id | Violation query (shape) |
|---------|-------------------------|
| `skill_ref` | skill_bindings with no `skills` row in the same workspace |
| `tool_ref` / `mcp_tool_ref` | tool_bindings whose non-null target has no same-workspace row |
| `kb_ref_ws` | retrieval_bindings whose KB is in another workspace |
| `chunk_orphan` / `chunk_dim` | kb_chunks with no same-workspace KB / `vector_dims ≠ embedding_dim` |
| `tool_target` | tool_bindings violating B3 |
| `active_version_agent` | agents whose active version belongs to another agent |

Any count > 0 ⇒ `RuntimeError("0015 preflight refused: <rule>=<count> ids=[…≤20]")` → transaction aborted, nothing
applied. Report carries ids only (no prompt/document text).

### B7. ORM (`api/models.py`) mirrors
Add `ForeignKeyConstraint`/`UniqueConstraint`/`CheckConstraint` entries to `__table_args__` and `retry_count` on `Run`,
so `Base.metadata` matches the migration (enforced by the new ORM-parity case in `test_migration_0015.py` — no metadata-parity test existed before 025).

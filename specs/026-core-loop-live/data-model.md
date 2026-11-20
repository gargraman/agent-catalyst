# Data Model — 026 Core Loop Live

One additive migration: **`0016_core_loop`** (down: `0015`). Every new table follows the 0008 tenant pattern:
- `workspace_id uuid NOT NULL REFERENCES workspaces(id)`
- `ix_<t>_workspace`
- `ENABLE` + `FORCE ROW LEVEL SECURITY`
- policy `<t>_ws_isolation` on `app.workspace_id`
- grants to `ac_app`

Cross-table references use **composite `(workspace_id, id)` FKs**, because RI bypasses RLS (025 G8). They are
`ON DELETE RESTRICT` unless stated otherwise. The ORM mirrors everything in `api/models.py`.

**Data classification**: eval case inputs, expected outputs and candidate outputs are **customer content**. They
get the same class as run inputs/outputs: tenant-isolated under RLS, never sent to analytics, never logged. Retention
follows the workspace (deleted with the workspace; no separate TTL in v1, Nice). Audit rows hold ids and actor
subjects only, no content.

## Changed tables

### `agents` (D1, D4)
| Column | Type | Notes |
|---|---|---|
| `description` | `text NOT NULL DEFAULT ''` | ≤ 2000 chars (API-validated) |
| `draft` | `jsonb NULL` | `NULL` ⇒ resolved from the active version on read. Shape is the **Draft** value object below |
| `status_before_archive` | `agent_status NULL` | Set on archive, cleared on restore. Exposed as `AgentOut.restore_status` |

The `xmin` ETag (existing) now guards description, draft, status, active version and access policy together.

### `agent_versions` (D2)
| Column | Type | Notes |
|---|---|---|
| `created_by` | `text NULL` | Principal subject at INSERT. `NULL` for pre-0016 rows. The freeze trigger is unaffected (set on insert, never updated) |

## Value object — Draft (stored in `agents.draft`)

```text
Draft {
  provider: str            # must be a known provider (same validator as AgentCreate)
  model: str               # must exist in the capability profiles for provider
  model_params: dict       # temperature/max_tokens/... validated against the profile (same as create)
  system_prompt: str
  io_contract: dict        # must contain input_schema (same rule as AgentCreate)
  kb_ids: [uuid]           # sorted, unique
  tool_ids: [uuid]         # sorted, unique (registry tools; MCP/subagent bindings out of scope for draft edit in v1)
  skill_ids: [uuid]        # sorted, unique
}
```

- **Normalization** (for `draft_dirty`): sort the id lists, drop default-valued params, compare with the active
  version projected onto the same shape. `draft_dirty = normalized(draft) != normalized(active)`. When `draft IS
  NULL`, `draft_dirty = false`.
- **Validation at save** checks shape and existence of provider/model. Binding ids are **not** checked at save,
  because a KB may be deleted later anyway. They are checked when a version is created (FR-005, 404 with `detail`)
  and at draft-run time.

## New tables

### `eval_suites`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `workspace_id` | uuid | |
| `agent_id` | uuid | FK `(workspace_id, agent_id) → agents(workspace_id, id)` RESTRICT |
| `name` | `text NOT NULL` | 1–80 chars, `UNIQUE (agent_id, name)` |
| `created_at`, `updated_at` | timestamptz | |

### `eval_cases`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | stable across edits (identity for D8) |
| `workspace_id` | uuid | |
| `suite_id` | uuid | FK `(workspace_id, suite_id) → eval_suites` **ON DELETE CASCADE** (a case has no meaning without its suite) |
| `input` | `jsonb NOT NULL` | must validate against nothing at save. Validated by the runtime at run time against the version io_contract (P6) |
| `rubric` | `jsonb NOT NULL` | `[{name: str, weight: float>0}]`, 1–8 dimensions |
| `required_fields` | `text[] NOT NULL DEFAULT '{}'` | deterministic hard assertion (engine-supported) |
| `expected` | `jsonb NULL` | optional reference output |
| `content_hash` | `text NOT NULL` | sha256 of the canonical JSON of (input, rubric, required_fields, expected). Server-computed on every write |
| `created_at`, `updated_at` | timestamptz | |

### `eval_runs`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `workspace_id` | uuid | |
| `agent_id` | uuid | FK → agents (composite) |
| `suite_id` | uuid | FK → eval_suites (composite) RESTRICT, so a suite with runs cannot be hard-deleted. Suite delete is refused with 409 `eval.suite_has_runs` (archive is Nice) |
| `version_id` | uuid | FK `(version_id, agent_id) → agent_versions(id, agent_id)`, so it must be this agent's version |
| `status` | `text CHECK IN ('completed','failed')` | |
| `verdict` | `text NULL CHECK IN ('pass','fail','low_confidence')` | NULL iff failed |
| `reason_code` | `text NOT NULL` | `ok, quality_drift, cost_drift, hard_fail, below_min_cases, no_baseline, baseline_stale, judge_unavailable` for completed runs; `timeout, error` for failed ones |
| `reason` | `text NOT NULL` | human-readable, fixed templates (no provider text) |
| `baseline_run_id` | uuid NULL | FK → eval_runs (composite) RESTRICT. NULL ⇒ no baseline |
| `baseline_origin` | `text NULL CHECK IN ('automatic','frozen')` | NULL iff `baseline_run_id` NULL |
| `quality`, `quality_delta`, `cost_usd`, `cost_delta_pct`, `latency_ms`, `latency_delta_ms` | numeric NULL | Deltas NULL without a baseline |
| `dimensions` | `jsonb NOT NULL DEFAULT '[]'` | `[{name, candidate, baseline?, delta?}]` |
| `compared_cases`, `new_cases`, `dropped_cases`, `total_cases` | int NOT NULL | D8 |
| `pairwise_rate` | numeric NULL | |
| `policy` | `jsonb NOT NULL` | the resolved policy actually applied (evidence) |
| `judge_version` | text NULL | e.g. `judge-v1` (constitution §4 identity) |
| `created_by` | text NOT NULL | principal subject |
| `started_at`, `finished_at` | timestamptz | |

Index: `(agent_id, finished_at DESC)`, `(suite_id, version_id, finished_at DESC) WHERE status='completed'` (the
automatic-baseline lookup), and `(workspace_id, finished_at DESC)`.

### `eval_results`
| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `workspace_id` | uuid | |
| `eval_run_id` | uuid | FK → eval_runs (composite) **ON DELETE CASCADE** |
| `case_id` | uuid | **not** an FK. A case may later be edited or deleted, but the result stays as evidence |
| `case_hash` | text NOT NULL | `content_hash` at run time (D8 identity) |
| `run_id` | uuid NULL | the per-case `runs` row (cost/latency provenance) |
| `passed_deterministic` | bool NOT NULL | |
| `quality` | numeric NULL | NULL when the judge was unavailable for this case |
| `rubric_scores` | jsonb NOT NULL DEFAULT '{}' | |
| `pairwise_winner` | text NULL | |
| `judge_low_confidence` | bool NOT NULL | |
| `failure_reason` | text NULL | fixed templates |
| `hard_failure` | bool NOT NULL | |
| `candidate_output` | text NOT NULL | feeds a future comparison's pairwise judge (customer content) |
| `cost_usd` | numeric NULL, `cost_is_estimate` bool | |
| `latency_ms` | int NULL | |
| `in_comparison` | bool NOT NULL | false for "new" cases (D8) |

`UNIQUE (eval_run_id, case_id)`.

### `eval_baselines` (frozen pointer; absence ⇒ automatic rule)
| Column | Type | Notes |
|---|---|---|
| `suite_id` | uuid **PK** | one frozen baseline per suite. FK → eval_suites (composite) CASCADE |
| `workspace_id` | uuid | |
| `eval_run_id` | uuid | FK → eval_runs (composite) RESTRICT, must be `status='completed'` and the same suite (route-checked + trigger `eval_baselines_run_check`) |
| `frozen_by` | text NOT NULL | |
| `frozen_at` | timestamptz NOT NULL | |

Freeze = upsert. Clear = delete. Each writes an `audit_logs` row in the same transaction.

### `audit_logs` (append-only)
| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `workspace_id` | uuid NOT NULL | |
| `actor` | text NOT NULL | principal subject |
| `action` | text NOT NULL | `baseline.freeze, baseline.clear, agent.publish, agent.repoint, agent.archive, agent.restore` |
| `target_type`, `target_id` | text, uuid | |
| `meta` | jsonb NOT NULL DEFAULT '{}' | ids only (e.g. `{previous_run_id, new_run_id}`, `{from_version_id, to_version_id}`) |
| `created_at` | timestamptz | |

Grants: `SELECT, INSERT` only (no UPDATE/DELETE) to `ac_app`. Index `(workspace_id, created_at DESC)`.

## State transitions

**Agent status** (existing enum `draft | published | archived | eval_failing`):

```text
draft ──publish──▶ published ──archive──▶ archived ──restore──▶ (status_before_archive)
  └──────────────────archive─────────────────▲
eval_failing ──archive──▶ archived            (restore puts back exactly what was there)
```

**Baseline for a suite** (resolved per run, D7):

```text
          freeze(run R)                 clear
 AUTOMATIC ───────────────▶ FROZEN(R) ─────────▶ AUTOMATIC
     ▲                          │ freeze(run S)
     └──────────────────────────┘ (re-freeze replaces R with S; audited with previous id)
AUTOMATIC = latest completed run of (suite, agents.active_version_id), else NONE
```

**Eval run**: `(in request) → completed | failed`. Rows are never updated after insert.

## Migration rules

- **Preflight**: nothing to refuse. All changes are additive, and the new tables are empty.
- **Downgrade** drops the new tables, then the new columns, and round-trips cleanly (test).
- **Upgrade/downgrade** are covered by `test_migration_0016.py` (integration), including RLS isolation and the
  append-only grant on `audit_logs` (an UPDATE as `ac_app` must fail with 42501).

# Research — Feature 025 Platform Hardening (Phase 0)

All decisions below were made against the code on disk at 2026-11-15 (not against design docs). Evidence that was
**verified live** is marked ✅; everything else is a design decision to be pinned by a Red test before Green.

## Ground truth found while planning (changes the design)

| # | Finding | Evidence | Consequence |
|---|---------|----------|-------------|
| G1 | **Hidden SDK retries already exist on the prod path.** litellm's OpenAI handler defaults `max_retries=DEFAULT_MAX_RETRIES` (=2, `openai/_constants.py`) when the caller passes none; `transport.stream_chunks` passes none. | ✅ `litellm/llms/openai/openai.py:388`, `openai/_constants.py:10` | OpenAI calls silently retry up to 2× today, invisible to cost/metrics/run record. Our retry loop on top would give up to **9** attempts. The transport MUST force `max_retries=0` + `num_retries=0` (pinned by `test_transport.py`). |
| G2 | litellm `RateLimitError.headers` is **only** the proxy-supplied kwarg; vendor headers (incl. `retry-after`) live on `exc.response.headers` (`httpx.Response`). Synthesized minimal responses have no headers. | ✅ `litellm/exceptions.py:484-498` | `Retry-After` extraction reads `exc.headers` then `exc.response.headers`; absence → `None` → backoff schedule. |
| G3 | `agent_versions_freeze` (F15) freezes **every** version row from insert — there is no "draft version". | ✅ `0002_rls_and_immutability.py:47-70` | "Frozen version" (spec Assumption) = every version. Binding immutability applies to **all** bindings once their version's creating transaction commits. |
| G4 | Versions + their bindings are written in **one transaction** (`create_version` clones, builds bindings, single `flush`). No savepoint on the console plane; the only `begin_nested()` (`deps.py:141`) is a released savepoint on the caller plane touching `api_tokens`. | ✅ `api/routers/agents.py:331-382`, `api/deps.py:141` | "Insert allowed only in the version's creating transaction" is implementable with `xmin = pg_current_xact_id()::xid`. |
| G5 | On PG16, `xmin = pg_current_xact_id()::xid` is **true** for a row inserted by the current top-level txn and **false** for a row inserted inside a SAVEPOINT. | ✅ probe on `agent-catalyst-dev-postgres-1` (pgvector:pg16) | Savepoint-wrapped version creation would be **rejected** (fail-closed, safe direction) — documented limitation, pinned by a test. |
| G6 | Only **retrieval** bindings are written by any API path. Skill/tool bindings have **no writer** today (only fixtures/tests). | ✅ grep `SkillBinding(` / `ToolBinding(` in `api/` | FR-016–FR-019 for skill/tool are **data-layer only**; there is no API route to map. FR-023 mapping applies to retrieval bindings, active-version set, and KB/chunk writes. |
| G7 | `FORCE ROW LEVEL SECURITY` is set on the registries/runs/bindings/RAG tables. A non-superuser owner running the preflight would see **zero rows** (silent false "clean"). Dev/CI `ac` is the image superuser (bypasses RLS). | ✅ `0004/0005/0008/0013` | Preflight runs `SET LOCAL row_security = off` → Postgres **errors** for a role that cannot bypass RLS instead of silently filtering (fail loud). |
| G8 | Postgres referential-integrity checks run with RLS bypassed. | Postgres docs (RI triggers run as owner, ignore RLS) | A single-column FK would let a binding reference a **different workspace's** skill. FKs MUST be composite `(workspace_id, ref_id) → (workspace_id, id)`. |
| G9 | `kb_chunks.kb_id` has **no FK at all**; `embedding` is a fixed `vector(1024)` column while `knowledge_bases.embedding_dim` is a free integer. | ✅ `api/models.py:266-275`, `186-192` | Any non-1024 vector is already rejected by the column type; the real F17 gap is a KB declaring `embedding_dim ≠ 1024` (or a chunk under the wrong KB). Needs a cross-table trigger + the missing FK. |
| G10 | `runs.outcome` is free `Text`; no retry column. `ProviderUnavailable` (502 `provider.unavailable`) already exists in `api/errors.py:137`. | ✅ | New outcome needs no enum migration; `retry_count` needs a column. Reuse the existing error class. |
| G11 | `fakeredis 2.40` is installed; `lupa` is **not** (ratelimit.py deliberately avoids Lua). | ✅ `pip list` | Breaker store uses only single commands + `MULTI/EXEC` pipelines — no Lua. |
| G12 | Eval runner is **sequential** per case; judge goes through the instance `evals/breaker.CircuitBreaker`. | ✅ `evals/runner.py` | Batch concurrency comes from concurrent eval requests + ingestion jobs, not intra-suite fan-out. The instance breaker is superseded by the shared batch-class breaker (kept as-is for the judge's advisory fail-closed; see D7). |

## Decisions

### D1 — Where retry + breaker live: a pure `runtime/providers/resilience.py` wrapped by the gateway
- **Decision**: New pure module `runtime/providers/resilience.py` exporting `ResiliencePolicy`, `BreakerStore` (Protocol),
  `LocalBreakerStore`, `Guard`, and the attempt loops used by `gateway.stream` and `gateway.embed`. The gateway takes an
  optional `guard: Guard | None`; `None` ⇒ a process-default guard (retry policy + local breaker), so a caller that
  forgets to inject still gets retries. The API injects a guard backed by the shared store.
- **Rationale**: Every caller (playground, published, channel, eval case, judge, query embedding, ingestion embedding)
  already funnels through `gateway.stream`/`gateway.embed` → one enforcement point = P6 parity by construction (FR-014).
  Putting it in `invoke._pipeline` would duplicate it across pass-through + validated paths and miss `gateway.embed`.
- **Alternatives**: (a) in `invoke._pipeline` — rejected, two code paths + misses ingestion; (b) litellm's own
  `num_retries` / Router — rejected: opaque, retries mid-stream, no shared breaker, cost invisible (G1 is the proof);
  (c) a tenacity/pybreaker dependency — rejected: neither supports "retry only before first token" on an async
  generator nor a shared Redis breaker; ~150 LOC of our own is smaller than the adapter glue.

### D2 — Retry only before the first surfaced event (F10)
- **Decision**: The gateway buffers nothing; it tracks `surfaced = False` and flips it on the first `TEXT_DELTA`,
  `REASONING_DELTA`, or any `TOOL_CALL_*` event. A `TransportError` with `retryable=True` while `surfaced` is False ⇒
  retry; once `surfaced` is True ⇒ the existing single normalized ERROR (no retry, FR-005). The degraded "no usage"
  ERROR is never retried (already `retryable=False`).
- **Rationale**: Zero duplicate output by construction; no latency cost (no buffering). Validated mode buffers in
  `invoke`, but the gateway rule is the same so behaviour is identical across modes.
- **Alternatives**: buffer the whole turn and retry anytime — rejected: breaks pass-through streaming latency (023 SLI).

### D3 — Retry policy + Retry-After (F11)
- **Decision**: `max_attempts=3` (total), exponential backoff `base=0.5s × 2^(n-1)`, full jitter `uniform(0, b)`, per-wait
  cap `8s`. `TransportError` gains `retry_after: float | None` parsed at the seam (G2): integer/float seconds or
  HTTP-date → seconds-from-now; `≤0`, past date, NaN/inf, non-numeric ⇒ `None`. Wait = `min(retry_after or jittered
  backoff, per_wait_cap)`. **Deadline rule**: before sleeping, if `elapsed + wait + min_attempt_budget(0.5s) ≥
  deadline` ⇒ stop retrying and surface the last error (no doomed attempt). Sleep and clock are injected (tests run in
  0 wall time). Embedding calls with no run deadline (ingestion) are bounded by attempts × cap only.
- **Rationale**: SC-002 (deadline + 1 s) holds even without the outer `asyncio.timeout`; with it (invoke), the outer
  bound is defence in depth.
- **Alternatives**: honour Retry-After uncapped — rejected (adversarial hang); ignore it — rejected (hammers a 429).

### D4 — What counts as transient (breaker + retry input)
- **Decision**: Reuse the seam's existing `_is_retryable` (408/409/429/≥500 + name hints) — **minus 409**: a provider
  409 is a request conflict, not transient. Breaker counts only `retryable=True` failures; FileNotFoundError,
  ModelRetiredError, context overflow, invalid input, 4xx never touch it (spec Edge Case).
- **Note**: removing 409 is a behaviour change to `_is_retryable`; pinned by `test_transport.py` Red.

### D5 — Breaker algorithm + shared store without Lua (FR-007/008)
- **Decision**: Keys per `(provider, model, class)` (no tenant data): `ac:cb:{p}:{m}:{c}:fails` (INCR, PEXPIRE 10 min),
  `…:open` (marker, `SET … PX cooldown`), `…:trial` (`SET … NX PX trial_ttl`).
  - `before_call`: `open` exists ⇒ **refuse**. Else if `fails ≥ N` (cooldown elapsed, not yet healed) ⇒ half-open:
    `SET trial NX PX trial_ttl` — winner proceeds as the single trial, losers refused.
  - `on_success`: `DEL fails trial` (closes).
  - `on_failure(transient)`: pipeline `INCR fails` + `PEXPIRE`; if result ≥ N ⇒ `SET open 1 PX cooldown`; if this was
    the trial ⇒ `SET open … PX cooldown` + `DEL trial`.
  - Defaults N=5, cooldown 30 s, `trial_ttl = deadline + 5 s` (a crashed trial frees itself).
- **Rationale**: Every step is one atomic command or a `MULTI/EXEC` pipeline → no lost counts under concurrency
  (FR-008) and fakeredis-testable without Lua (G11). The `open` marker's TTL *is* the cooldown, so there is no
  clock-skew between instances.
- **Alternatives**: Lua script — rejected (no `lupa`; ratelimit precedent); single HASH with HINCRBY + timestamps —
  rejected: half-open single-flight then needs compare-and-set (Lua/WATCH retry loops).

### D6 — Traffic classes + bulkhead (F8, FR-010/011)
- **Decision**: `class = "batch"` for `source ∈ {eval}` and for ingestion embeddings; `"live"` for `playground, api,
  channel, tool_test, subagent`. The judge runs with the eval ctx ⇒ batch. Query embeddings inherit the run's class.
  A batch call checks **both** its own breaker and the live `open` marker (live-open refuses batch); a live call checks
  only live keys. Failures increment only the caller's class keys.
  Batch concurrency cap = per-process `asyncio.Semaphore` per `(provider, model)` (default 4), acquired **before** the
  breaker check and held for the attempt loop; waiting is queueing, never failure, but still bounded by the deadline.
- **Rationale**: Per-process semaphore is honest for the pilot's single replica; the effective cap is `cap × replicas`
  — documented, not hidden. A distributed semaphore is Nice (scale-out, M11).
- **Alternatives**: Redis semaphore — rejected for now (lease/TTL complexity for a one-replica pilot).

### D7 — Fate of `evals/breaker.py`
- **Decision**: Keep it (it is the judge's *advisory* fail-closed wrapper and has its own tests), but the provider
  breaker now sits underneath it in the gateway. No behaviour change to the runner; docs note the layering.
- **Alternatives**: delete — rejected for this feature (scope creep; it guards non-provider judge exceptions too).

### D8 — Shared-store outage (FR-009)
- **Decision**: Any store exception ⇒ the guard switches that call to the process-local `LocalBreakerStore` (same
  algorithm, in memory) and increments `ac_breaker_degraded_total`. Never refuses *because* Redis is down. Added as a
  new row in `docs/backend/degraded-mode.md` (availability control → fail open + alert, parity with the limiter).
- **Note**: `AC_REDIS_URL` unset ⇒ local store, **not** degraded (parity with ratelimit's no-op seam).

### D9 — How retries + refusals reach the run record (FR-006/015)
- **Decision**: The gateway puts `attempts` on the terminal `USAGE` and `ERROR` event payloads (additive field). A
  breaker refusal is an ERROR with `type="provider_unavailable"`, `retryable=True`, and no provider contact.
  `Outcome.PROVIDER_UNAVAILABLE = "provider_unavailable"` is added; `RunRecord.retries: int = 0` (= Σ(attempts−1) over
  turns + embedding calls). Cost: a retried attempt failed **before** any output and before USAGE, so it reported no
  usage ⇒ contributes 0 (FR-015 holds by construction; the mid-stream case that *can* bill is never retried).
- **Persistence**: `runs.retry_count int NOT NULL DEFAULT 0` (migration `0014_runs_retry_count`); `RunOut.retries`; outcome string is free
  text (G10). `ModelCallRecord` is unchanged (per-attempt call records = Nice).

### D10 — Integrity enforcement shape (FR-016–FR-021)
- **Decision** (migration `0015_binding_integrity`; split from the retry_count column at task time so US4 ships independently):
  1. `UNIQUE (workspace_id, id)` on `skills`, `tools`, `mcp_tools`, `knowledge_bases`; `UNIQUE (id, agent_id)` on
     `agent_versions` (FK targets).
  2. Composite FKs, `ON DELETE RESTRICT` (FR-017 = "referenced ⇒ not hard-deletable"; archive untouched):
     `skill_bindings(workspace_id, skill_id)→skills`, `tool_bindings(workspace_id, tool_id)→tools`,
     `tool_bindings(workspace_id, mcp_tool_id)→mcp_tools`, `retrieval_bindings(workspace_id, kb_id)→knowledge_bases`,
     `kb_chunks(workspace_id, kb_id)→knowledge_bases`. `MATCH SIMPLE` so a NULL target column is not checked.
  3. `agents(active_version_id, id) → agent_versions(id, agent_id)` **DEFERRABLE INITIALLY DEFERRED** (agent and first
     version are created in one txn with a circular reference).
  4. `CHECK tool_bindings_one_target`: `http|builtin ⇒ tool_id only`, `mcp ⇒ mcp_tool_id only`, `subagent ⇒
     subagent_config_id only`.
  5. Immutability: `BEFORE UPDATE OR DELETE` on the three binding tables ⇒ RAISE; `BEFORE INSERT` ⇒ RAISE unless the
     parent version's `xmin = pg_current_xact_id()::xid` (G4/G5). `ERRCODE = 'restrict_violation'` (23001, integrity
     class → `IntegrityError`) with message prefix `frozen_binding:`.
  6. `kb_chunks` `BEFORE INSERT OR UPDATE OF embedding, kb_id` ⇒ RAISE `check_violation` (23514) prefix
     `chunk_dim_mismatch:` when `vector_dims(NEW.embedding) <> kb.embedding_dim`.
- **Rationale**: All guarantees live in Postgres (FR-016 "data layer, not only API"); composite FKs are the only
  RLS-proof same-workspace guarantee (G8).
- **Alternatives**: triggers for references — rejected (FKs are declarative, indexed, and faster); `CHECK
  embedding_dim = 1024` on KBs — rejected: hard-codes the 1024 decision into the schema; the trigger stays correct if a
  second dimension is ever added. Subagent reference: unconstrained (no catalog; spec Assumption).

### D11 — Preflight refuses, never repairs (FR-022)
- **Decision**: `upgrade()` first runs `SET LOCAL row_security = off` (G7) then one `COUNT` + up-to-20 offending ids per
  rule (dangling skill/tool/mcp/kb ref, cross-workspace ref, multi-target tool binding, cross-agent active version,
  chunk dim mismatch, chunk orphan). Any non-zero ⇒ `RuntimeError` listing rule → count → ids (ids only, no content);
  Alembic aborts the transaction, nothing is applied. `downgrade()` drops everything 0015 added.

### D12 — API error mapping (FR-023)
- **Decision**: New `api/db_errors.py: map_integrity_error(exc) -> AppError` keyed on SQLSTATE + constraint name /
  message prefix: `23503` FK on a binding/chunk ⇒ `404 not_found` (resource not found in this workspace — no
  existence leak across tenants); `23001 frozen_binding` ⇒ `409 version.frozen` (new `VersionFrozen(ConflictError)`);
  `23514 tool_bindings_one_target` / `chunk_dim_mismatch` ⇒ `400 input.invalid`; `23503` on `agents` active version ⇒
  `400 input.invalid`. Anything unmapped re-raises (honest 500). Applied at the write sites reachable today:
  `create_version` (retrieval bindings), the active-version set (publish/promote), and the ingestion chunk write
  (job fails with typed `chunk_dim_mismatch`, not a 500).

### D13 — Runtime purity + metrics plumbing
- **Decision**: The runtime never imports redis/prometheus. `Guard` takes `store: BreakerStore`, `observer:
  Callable[[str, dict], None] | None`, `clock`, `sleep`, `rng`. The API builds one `Guard` per process
  (`api/resilience_wiring.py`) with `RedisBreakerStore` (`api/breaker_store.py`) and an observer that bumps
  `api/metrics.py` counters. `RunContext.guard` carries it; `ingest_executor` passes it to `gateway.embed`.
  Label set: `provider, model, class, to/state` — bounded by the profile registry, no tenant content (FR-013).

## Open items (non-blocking, recorded with owner + validation point)

| Item | Owner | Validation point |
|------|-------|------------------|
| Default thresholds (3 / 0.5 s / 8 s / N=5 / 30 s / cap 4) are unvalidated against real traffic | platform owner | first live provider week; tune via settings |
| Effective batch cap = cap × replicas | platform owner | M11 scale-out (distributed semaphore = Nice) |
| Savepoint-wrapped version creation is rejected (G5) | platform | pinned by test; revisit only if a nested-txn writer appears |
| `_is_retryable` dropping 409 | platform | Red test in `test_transport.py` |

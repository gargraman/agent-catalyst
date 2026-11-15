---
description: "Task list — feature 025 platform hardening (Must tier, scope items 1–7)"
---

# Tasks: Platform Hardening — Provider Resilience + Binding Integrity

**Input**: `specs/025-platform-hardening/` — plan.md, spec.md, research.md (G1–G12, D1–D13), data-model.md,
contracts/runtime-resilience.md (S*/B*/K*), contracts/api-and-data.md, quickstart.md

**Tests**: MANDATORY. Constitution §9 requires strict Red→Green→Refactor: every behaviour-changing task has a Red test
task before it. **Red evidence (failing for the expected reason — not import/syntax errors) is shown to the owner and
confirmed before the matching Green task starts.** Never weaken, skip, or xfail a test to get Green.

**Scope**: Must tier only (spec items 1–7). Items 8–13 are out of scope — do not implement them opportunistically.

**Paths**: backend = `services/` (run commands from `services/` with `./.venv/bin/python -m pytest …`); integration
tests need `make up` (Postgres :5433, Redis :6380). No live/paid provider calls anywhere — fake transport only.

**Migration split (decided at task time; plan/data-model/contracts/quickstart updated to match):** `0014_runs_retry_count`
(US1, trivial) and `0015_binding_integrity` (US4: preflight + uniques + FKs + CHECK + triggers), so the stories ship
independently and US4's preflight can never block US1.

## Format: `[ID] [P?] [Story] Description`
- **[P]** = different files, no dependency on an incomplete task. **[USn]** = user story from spec.md.

---

## Phase 1: Setup

- [X] T001 Record the pre-025 baseline in this file's "Evidence" section: `./.venv/bin/python -m pytest -q --collect-only | tail -1`, `-m "not integration"` and `-m integration` pass/skip/xfail counts, and `alembic current` (must be `0013`). No code changes.
- [X] T002 [P] Confirm the fake-transport seams the new tests will use: `transport.stream_chunks` / `transport.embed` monkeypatch pattern in `services/tests/test_gateway.py` and `services/tests/test_gateway_embed.py`, and the fakeredis fixture pattern in `services/tests/test_redis_degraded_mode.py`; add a shared helper `services/tests/_fault_transport.py` (scripted per-attempt outcomes: `raise TransportError(retryable, status, retry_after)` / `yield chunks then raise` / `yield chunks + usage`, with a call counter). Test scaffolding only — implements no production behaviour.

---

## Phase 2: Foundational (blocks all stories)

**Purpose**: the transport seam must stop hiding retries (G1) and expose Retry-After before any retry loop exists; the pure resilience types every story uses.

- [X] T003 [P] Red: in `services/tests/test_transport.py` assert `stream_chunks` and `embed` pass `max_retries=0` and `num_retries=0` to litellm (via `setdefault`, so an explicit caller value is observable) — research G1. Focused: `pytest -q tests/test_transport.py -k retries`
- [X] T004 [P] Red: in `services/tests/test_transport.py` add a table test for `TransportError.retry_after` parsing — `exc.headers["retry-after"]` then `exc.response.headers` (case-insensitive); `"7"`→7.0, `"1.5"`→1.5, future HTTP-date→seconds-from-now (injected now), past date / `"0"` / `"-3"` / `"nan"` / `"inf"` / `"soon"` / absent / synthesized minimal response → `None` (contract §6, research G2). Focused: `pytest -q tests/test_transport.py -k retry_after`
- [X] T005 [P] Red: in `services/tests/test_transport.py` assert `_is_retryable` → False for status 409; still True for 408, 429, 500, 503 and for name hints `Timeout`, `APIConnectionError` (research D4). Focused: `pytest -q tests/test_transport.py -k retryable`
- [X] T006 Green: implement T003–T005 in `services/runtime/providers/transport.py` (`kwargs.setdefault("max_retries", 0)`, `kwargs.setdefault("num_retries", 0)` in both functions; `TransportError.__init__(…, retry_after: float | None = None)`; `_retry_after(exc)` helper; remove 409 from the transient set). Regression: `pytest -q tests/test_transport.py tests/test_gateway.py tests/test_gateway_embed.py`
- [X] T007 [P] Red: create `services/tests/test_resilience_types.py` — `ResiliencePolicy()` defaults exactly `max_attempts=3, backoff_base_s=0.5, per_wait_cap_s=8.0, min_attempt_budget_s=0.5, failure_threshold=5, cooldown_s=30.0, batch_concurrency=4`; constructor rejects any numeric `≤ 0` and `max_attempts < 1`; `traffic_class("eval") == "batch"`; `traffic_class(s) == "live"` for `playground, api, channel, tool_test, subagent` and for an unknown source; `BreakerKey` has exactly the fields `provider, model, cls` (no tenant field). Focused: `pytest -q tests/test_resilience_types.py`
- [X] T008 Green: create `services/runtime/providers/resilience.py` with `ResiliencePolicy`, `TrafficClass`, `traffic_class()`, `BreakerKey`, `Admission`, `BreakerStore` Protocol (data-model §A1–A5) — no redis/prometheus/FastAPI imports. Regression: `pytest -q tests/test_resilience_types.py tests/test_runtime_purity.py`

**Checkpoint**: Red evidence for T003–T005, T007 shown to the owner → Green T006, T008 → foundational suite green.

---

## Phase 3: User Story 1 — A brief provider hiccup does not fail the user's run (P1) 🎯 MVP

**Goal**: bounded retry with backoff/jitter + capped Retry-After, only before the first surfaced event; retries recorded on the run.
**Independent test**: fault transport fails 2× transiently then succeeds → run `SUCCESS`, text delivered once, `retries == 2`, persisted `runs.retry_count == 2` (quickstart §1).

### Red tests (US1)
- [X] T009 [P] [US1] Red: create `services/tests/test_resilience_retry.py` driving `gateway.stream` with `_fault_transport` and an injected `Guard(clock, sleep, rng)` (sleep records waits, takes 0 wall time) — contract S1 (retry before first event, failed attempt yields nothing), S2 (`USAGE.data["attempts"] == k`), S3 (non-transient → 1 call, no retry), S4 (transient after a `TEXT_DELTA`, after a `REASONING_DELTA`, after a `TOOL_CALL_*` → single ERROR, 1 call, concatenated text equals what was streamed once), S5 (always failing → exactly 3 calls, `attempts == 3`), S6 (next wait would cross `deadline − 0.5 s` → stop early, last error surfaced), S7 (valid `retry_after=2` → wait 2.0; `retry_after=3600` → wait capped at 8.0; `None` → `uniform(0, 0.5·2^(n-2))` via seeded rng), S8 (usage-less stream → degraded ERROR, never retried). Focused: `pytest -q tests/test_resilience_retry.py`
- [X] T010 [P] [US1] Red: in `services/tests/test_gateway_embed.py` add — a batch that fails transiently once then succeeds is retried; vectors appear exactly once and in input order across batches; `EmbeddingResult.attempts` sums attempts over batches; exhausted attempts → `EmbeddingError(type="provider_error", retryable=True, attempts=3)`; non-transient → 1 call. Focused: `pytest -q tests/test_gateway_embed.py -k attempt`
- [X] T011 [P] [US1] Red: create `services/tests/test_invoke_resilience.py` — through `runtime.invoke_result`: (a) fail×2 then success → `Outcome.SUCCESS`, `RunRecord.retries == 2`, `cost_usd` equals the priced *reported* usage only — failed attempts with no reported usage add 0 (FR-015 as amended: disclosed via `retries`, never asserted as the exact provider charge); (b) validated mode with one correction turn where each turn retries once → `retries == 2`; (c) a retried query embedding adds to `retries`; (d) `retry_after=3600`, `ctx.deadline_s=30` → run ends `TIMEOUT` or `PROVIDER_ERROR` within 31 s of injected clock (SC-002), never hangs; (e) non-transient → `PROVIDER_ERROR`, `retries == 0`; (f) transient failure after a `TEXT_DELTA` → `Outcome.PROVIDER_ERROR` (pinned, FR-005 — not `DEGRADED`), `retries == 0`, delivered text not duplicated. Focused: `pytest -q tests/test_invoke_resilience.py`
- [X] T012 [P] [US1] Red (integration): create `services/tests/test_api_runs_retries.py` — after `alembic upgrade head`, the `runs` table has `retry_count integer NOT NULL DEFAULT 0`; a playground invoke with a fault transport (fail×2 then success) persists `retry_count = 2` and `GET /v1/runs/{id}` returns `"retries": 2`; a clean run returns `"retries": 0`; the OpenAPI document's `RunOut.retries` and `RunOut.cost_usd` descriptions contain "reported usage" (FR-015 disclosure). Focused: `pytest -q -m integration tests/test_api_runs_retries.py`

### Green (US1)
- [X] T013 [US1] Green: in `services/runtime/providers/resilience.py` add `Guard` (policy, store, local_store, observer, clock=`time.monotonic`, sleep=`asyncio.sleep`, rng=`random.Random()`), the retry-wait computation (D3: `min(retry_after or jittered backoff, per_wait_cap_s)`, deadline rule with `min_attempt_budget_s`), and a lazily built module `default_guard()`. Breaker/bulkhead hooks are no-ops until US2/US3. Focused: T009
- [X] T014 [US1] Green: in `services/runtime/providers/gateway.py` wrap `stream()` in the attempt loop (track `surfaced` on the first TEXT/REASONING/TOOL_CALL event; retry only on `retryable` `TransportError` while not surfaced; add `"attempts"` to the terminal USAGE and ERROR `data`) and `embed()` per batch request; add `guard: Guard | None = None, traffic_class: TrafficClass = "live"` params (contract §1). In `services/runtime/providers/ir.py` add `attempts: int = 1` to `EmbeddingResult` and `EmbeddingError`. Focused: T009, T010; regression: `pytest -q tests/test_gateway.py tests/test_gateway_embed.py tests/test_anthropic_adapter.py`
- [X] T015 [US1] Green: `services/runtime/context.py` add `guard: "Guard | None" = None`; `services/runtime/run.py` add `RunRecord.retries: int = 0`; `services/runtime/invoke.py` pass `guard=ctx.guard, traffic_class=traffic_class(ctx.source)` at both `gateway.stream` call sites (pass-through + `_collect_turn`), capture per-turn `attempts`, and set `retries = Σ(attempts−1)` over turns + embedding calls in `_build_run`; `services/runtime/retrieval/step.py` pass the same guard/class to `gateway.embed` and keep its `attempts`. Focused: T011; regression: `pytest -q tests/test_invoke_parity.py tests/test_invoke_*.py tests/test_retrieval_step.py tests/test_call_record_emission.py`
- [X] T016 [US1] Green: create `services/migrations/versions/0014_runs_retry_count.py` (`ALTER TABLE runs ADD COLUMN retry_count integer NOT NULL DEFAULT 0`; downgrade drops it); `services/api/models.py` `Run.retry_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")`; `services/api/run_sink.py` persist `retry_count=rec.retries`; `services/api/schemas/runs.py` `RunOut.retries: int = Field(description=…)` plus a `cost_usd` description stating the reported-usage-only basis, and the runs router mapping. Focused: T012; regression: `pytest -q -m integration tests/test_api_runs*.py tests/test_migration_*.py`

**Checkpoint US1**: quickstart §1 rows US1-1…US1-5 pass; unit + integration suites green.

---

## Phase 4: User Story 2 — A down provider fails fast instead of piling up (P1)

**Goal**: shared closed→open→half-open breaker per (provider, model, class); refusal = distinct zero-cost outcome; Redis outage → local fallback + alert.
**Independent test**: always-failing fault transport → 5 failures open the breaker; next call makes 0 transport calls in < 50 ms; after cooldown exactly 1 trial among concurrent callers across 3 Guards sharing one store (quickstart §1, §4).

### Red tests (US2)
- [X] T017 [P] [US2] Red: create `services/tests/test_resilience_breaker.py` with `LocalBreakerStore` + injected clock — contract B1 (5 consecutive transient failures → refused), B2 (cooldown elapsed, 10 concurrent `admit` → exactly 1 `trial`, 9 `refused`), B3 (trial success → closed), B4 (trial failure → open for another 30 s), B5 (model A open, model B admitted), B8 (a success between failures resets the count); S9 (refusal → ERROR `{type:"provider_unavailable", retryable:true, attempts:0}` with **0** transport calls and < 50 ms wall time); S10 (FileNotFoundError / ModelRetiredError raised before admission, store untouched); non-transient 4xx never increments the count. Focused: `pytest -q tests/test_resilience_breaker.py`
- [X] T018 [P] [US2] Red: create `services/tests/test_breaker_store.py` (unit, fakeredis) — `RedisBreakerStore` passes the same B1–B5/B8 scenarios; keys match `ac:cb:{provider}:{model}:{class}:{fails|open|trial}` and contain no workspace/agent id; 50 concurrent `record_failure` → `fails == 50` (no lost counts, FR-008); 3 `Guard` instances sharing one fakeredis → exactly 1 trial per cooldown window (SC-003); B9: a store whose methods raise → the call is still attempted via the local store and the observer receives `breaker_degraded`; `get_redis() is None` → API wiring selects `LocalBreakerStore` and emits no `breaker_degraded`. No Lua commands used (fakeredis without `lupa`). Focused: `pytest -q tests/test_breaker_store.py -m "not integration"`
- [X] T019 [P] [US2] Red: in `services/tests/test_invoke_resilience.py` add — breaker-open run → `Outcome.PROVIDER_UNAVAILABLE`, `usage is None`, `cost_usd` None/0, `retries == 0`, `error["type"] == "provider_unavailable"`; a refused query embedding degrades that retrieval binding (`degraded_reason="provider_unavailable"` — free-string field, no vocabulary change) without failing the run (feature 024 FR-020 parity); and in `services/tests/test_ingest_executor_unit.py` an ingestion embedding refused by the breaker fails the job with `error={"type":"provider_unavailable","retryable":true}`, writes no chunks, and raises nothing (FR-012). Focused: `pytest -q tests/test_invoke_resilience.py tests/test_ingest_executor_unit.py -k unavailable`
- [X] T020 [P] [US2] Red (integration): create `services/tests/test_api_invoke_breaker.py` — with the process Guard pre-opened for the agent's (provider, model, live): non-streaming playground/published invoke → `502 {"error":{"code":"provider.unavailable"}}`; SSE invoke → terminal `error` frame with `type: "provider_unavailable"`; the persisted run has `outcome = "provider_unavailable"`, `cost_usd = 0`; `ac_runs_total{outcome="provider_unavailable"}` increments. Focused: `pytest -q -m integration tests/test_api_invoke_breaker.py`
- [X] T021 [P] [US2] Red: in `services/tests/test_settings.py` add — `AC_PROVIDER_MAX_ATTEMPTS`, `AC_PROVIDER_BACKOFF_BASE_S`, `AC_PROVIDER_WAIT_CAP_S`, `AC_BREAKER_FAILURE_THRESHOLD`, `AC_BREAKER_COOLDOWN_S`, `AC_BATCH_CONCURRENCY` default to `3, 0.5, 8.0, 5, 30.0, 4`, map onto `ResiliencePolicy`, and any value `≤ 0` fails settings load (contract api §3). Focused: `pytest -q tests/test_settings.py -k provider or breaker or batch`

### Green (US2)
- [X] T022 [US2] Green: in `services/runtime/providers/resilience.py` add `LocalBreakerStore` (in-memory, same algorithm as D5 incl. trial lease TTL = deadline + 5 s) and wire admission/record into the `Guard` attempt loop: `admit` before the first attempt (refused → S9 ERROR, no transport call), `record_success` / `record_failure` (transient only) after each attempt, any store exception → use `local_store` for that call + observer `breaker_degraded`. Focused: T017; regression: `pytest -q tests/test_runtime_purity.py tests/test_resilience_*.py`
- [X] T023 [US2] Green: create `services/api/breaker_store.py` `RedisBreakerStore` over `redis_client.get_redis()` using only `EXISTS/GET/INCR/PEXPIRE/SET PX/SET NX PX/DEL` + `MULTI/EXEC` pipelines (contract api §5); `fails` key PEXPIRE 600 000 ms. Focused: T018
- [X] T024 [US2] Green: `services/runtime/run.py` add `Outcome.PROVIDER_UNAVAILABLE = "provider_unavailable"`; `services/runtime/invoke.py` map ERROR `type == "provider_unavailable"` to it in both modes; `services/runtime/retrieval/step.py` map `EmbeddingError(type="provider_unavailable")` to a degraded binding; `services/api/ingest_executor.py` map it to a retryable typed job failure. Focused: T019
- [X] T025 [US2] Green: `services/api/settings.py` add the six `AC_*` fields with `> 0` validation; create `services/api/resilience_wiring.py` (`get_guard()` process singleton: `ResiliencePolicy` from settings, `RedisBreakerStore` if `get_redis()` else `LocalBreakerStore`, `reset_for_tests()`); set `RunContext(guard=get_guard())` in `services/api/routers/invoke.py` (both planes via `_run_invoke`), `services/api/routers/evals.py`, and pass `guard=get_guard()` in `services/api/ingest_executor.py`; map a pre-stream `provider_unavailable` to `ProviderUnavailable` (502) in the non-streaming path. Focused: T020, T021
- [X] T026 [US2] Verification (integration, real Redis — exercises code already built in T023, so it is expected to pass on first run; recorded as verification evidence, **not** a Red→Green pair per constitution §9): in `services/tests/test_breaker_store.py` add `@pytest.mark.integration` cases against compose Redis (:6380) — 3 Guards, one Redis → exactly 1 trial per cooldown; a client pointed at a closed port → local fallback, call still attempted, `breaker_degraded` observed. Focused: `pytest -q -m integration tests/test_breaker_store.py`

**Checkpoint US2**: quickstart §1 US2-1…US2-5 and §4 pass.

---

## Phase 5: User Story 3 — Evaluation load cannot take down live traffic (P1)

**Goal**: live vs batch classes; batch has a per-process concurrency cap that queues; live-open refuses batch, batch-open never refuses live.
**Independent test**: drive the batch breaker open via eval-sourced calls, then a playground call on the same model is attempted; 20 concurrent batch calls never exceed 4 in flight (quickstart §1 US3).

### Red tests (US3)
- [X] T027 [P] [US3] Red: create `services/tests/test_resilience_bulkhead.py` — B6 (live open → batch refused, 0 calls), B7 (batch open → live admitted and attempted), K1 (20 concurrent batch calls on one (p, m) with a slow fake transport → max observed in-flight == 4, all eventually complete, observer `batch_wait` emitted), K2 (live calls never wait on the semaphore even when batch is saturated), K3 (a queued batch call whose deadline expires ends as `TIMEOUT` via invoke, not a hang); failures from batch traffic increment only `…:batch:fails`. Focused: `pytest -q tests/test_resilience_bulkhead.py`
- [X] T028 [P] [US3] Red: in `services/tests/test_invoke_resilience.py` / `services/tests/test_evals_runner.py` add — an eval run's candidate calls and judge calls reach the gateway with `traffic_class == "batch"`; ingestion embeddings (`services/api/ingest_executor.py`) reach `gateway.embed` with `"batch"`; a playground run's query embedding uses `"live"`. Focused: `pytest -q tests/test_invoke_resilience.py tests/test_evals_runner.py -k traffic`
- [X] T029 [P] [US3] Red: create `services/tests/test_resilience_parity.py` — one fault script (fail×1 then success) through playground, published (`source="api"`), channel (`source="channel"`), and an eval case → identical `RunRecord.retries`, outcome, `cost_usd`; only the class differs (FR-014/P6). Focused: `pytest -q tests/test_resilience_parity.py`

### Green (US3)
- [ ] T030 [US3] Green: in `services/runtime/providers/resilience.py` add the per-(provider, model) lazy `asyncio.Semaphore(batch_concurrency)` map, acquired before admission and held across the attempt loop for class `batch` only (observer `batch_wait` with seconds waited), and the cross-check `also_check=BreakerKey(p, m, "live")` for batch admissions. Focused: T027
- [ ] T031 [US3] Green: `services/api/ingest_executor.py` pass `traffic_class="batch"`; confirm `evals/runner.py` + `evals/judge.py` go through `invoke`/gateway with the eval ctx (`source="eval"` → batch) — change code only if T028 is Red for a real gap. Keep `evals/breaker.py` unchanged (research D7). Focused: T028, T029

**Checkpoint US3**: quickstart §1 US3-1…US3-3 and §2 parity pass.

---

## Phase 6: User Story 4 — What an evaluation certified is exactly what runs (P1)

**Goal**: data-layer integrity — same-workspace reference FKs with RESTRICT, binding immutability (F15 parity), exactly-one tool target, active-version-same-agent, chunk dimension; preflight refuses on dirty data.
**Independent test**: each US4 violation rejected at the data layer with a typed API error where reachable (quickstart §3).
*Independent of US1–US3 — may run in parallel with Phases 3–5 after Phase 2.*

### Red tests (US4)
- [ ] T032 [P] [US4] Red (integration): create `services/tests/test_binding_integrity.py` on a migrated DB — US4-1 binding → missing skill / tool / mcp tool / KB, and → a **same-id-shape row in another workspace**, each raises `IntegrityError` SQLSTATE `23503`; US4-2 `UPDATE` and `DELETE` of a skill/tool/retrieval binding whose version committed → `23001` with message prefix `frozen_binding:` and the version's `snapshot_hash` unchanged; a late `INSERT` of a binding for an already-committed version → `23001`; version + bindings in one transaction (the `create_version` shape) succeeds; version inserted inside a SAVEPOINT then a binding insert → rejected (research G5, pinned); US4-3 tool binding `kind='http'` with 0 targets and with `tool_id` + `mcp_tool_id` → `23514` `ck_tool_bindings_one_target`, `kind='subagent'` with only `subagent_config_id` → OK; US4-4 `agents.active_version_id` set to another agent's version → `23503` at COMMIT (deferred); US4-5 a KB with `embedding_dim=768` then a 1024-dim chunk → `23514` prefix `chunk_dim_mismatch:`; a chunk with a `kb_id` from another workspace → `23503`; US4-6 hard `DELETE` of a skill/tool/KB referenced by a binding → `23503`; archiving it (`archived_at=now()`) still succeeds; `TRUNCATE` of binding tables still works (test teardown). Focused: `pytest -q -m integration tests/test_binding_integrity.py`
- [ ] T033 [P] [US4] Red (integration): create `services/tests/test_migration_0015.py` — starting at `0014`, seed one violation per preflight rule (`skill_ref`, `tool_ref`, `mcp_tool_ref`, `kb_ref_ws`, `chunk_orphan`, `chunk_dim`, `tool_target`, `active_version_agent`) in turn → `alembic upgrade 0015` raises, the message lists `<rule>=<count>` and ≤ 20 ids and **no text content**, and `alembic current` is still `0014` with zero rows deleted; on a clean DB `upgrade 0015 → downgrade 0014 → upgrade 0015` round-trips and every 0015 object (5 uniques, 6 FKs, 1 CHECK, 3 trigger functions) exists after upgrade and is gone after downgrade; preflight executes `SET LOCAL row_security = off`; **ORM parity**: every constraint name 0015 adds to `pg_constraint` (uniques, FKs, CHECK) is also declared in `Base.metadata` (Red until T037). Focused: `pytest -q -m integration tests/test_migration_0015.py`
- [ ] T034 [P] [US4] Red: create `services/tests/test_db_errors.py` (unit, fake `IntegrityError` with `orig.sqlstate` + constraint name / message) — `23503` on `fk_skill_bindings_skill|fk_tool_bindings_tool|fk_tool_bindings_mcp_tool|fk_retrieval_bindings_kb_ws|fk_kb_chunks_kb` → `NotFound` (404 `not_found`); `23001` `frozen_binding:` → `VersionFrozen` (409 `version.frozen`); `23514` `ck_tool_bindings_one_target` or `chunk_dim_mismatch:` → `InputInvalid` (400); `23503` `fk_agents_active_version` → `InputInvalid` (400); unknown SQLSTATE/constraint → `None` (caller re-raises → honest 500); messages contain ids only. Focused: `pytest -q tests/test_db_errors.py`
- [ ] T035 [P] [US4] Red (integration): create `services/tests/test_api_integrity_errors.py` — `POST /v1/agents/{id}/versions` whose KB is hard-deleted between pre-check and flush (simulate by patching the pre-check) → 404 `not_found`; publish (`agents.py` raw update ~L241) and eval promote (`evals.py` ~L150) with a version id of another agent → 400 `input.invalid`; a direct binding update attempted through a test-only session → mapped 409 `version.frozen`; ingestion of a document into a KB whose `embedding_dim ≠ 1024` (KB row seeded via SQL, since the API now refuses it) → job `failed` with error type `chunk_dim_mismatch`, `retryable=false`, no 500; `POST` KB create with `embedding_dim: 768` → `400 input.invalid` (FR-021 create guard). Focused: `pytest -q -m integration tests/test_api_integrity_errors.py`

### Green (US4)
- [ ] T036 [US4] Green: create `services/migrations/versions/0015_binding_integrity.py` — order: (1) preflight with `SET LOCAL row_security = off`, 8 rules, `RuntimeError("0015 preflight refused: …")` on any violation (data-model §B6); (2) uniques `uq_skills_ws_id`, `uq_tools_ws_id`, `uq_mcp_tools_ws_id`, `uq_kb_ws_id` on `(workspace_id, id)` and `uq_agent_versions_id_agent` on `(id, agent_id)`; (3) FKs per data-model §B2, all `ON DELETE RESTRICT`, `fk_agents_active_version` `DEFERRABLE INITIALLY DEFERRED`; (4) `ck_tool_bindings_one_target` per §B3 verbatim; (5) trigger functions `bindings_freeze()` (BEFORE UPDATE OR DELETE → `RAISE … USING ERRCODE='restrict_violation'`, message `frozen_binding: …`), `bindings_insert_window()` (BEFORE INSERT → raise unless `(SELECT xmin FROM agent_versions WHERE id = NEW.version_id) = pg_current_xact_id()::xid`), `kb_chunks_dim_check()` (BEFORE INSERT OR UPDATE OF embedding, kb_id → `ERRCODE='check_violation'`, `chunk_dim_mismatch: …` when `vector_dims(NEW.embedding) <> kb.embedding_dim`) on the three binding tables / `kb_chunks`; downgrade drops all in reverse. Focused: T032, T033
- [ ] T037 [US4] Green: mirror 0015 in `services/api/models.py` `__table_args__` (`UniqueConstraint`, `ForeignKeyConstraint(..., ondelete="RESTRICT")`, `CheckConstraint`, deferrable FK on `Agent`), keeping `Base.metadata` in parity with the migration. Focused: T033 ORM-parity case; regression: `pytest -q -m integration tests/test_migration_*.py tests/test_binding_integrity.py`
- [ ] T038 [US4] Green: `services/api/errors.py` add `class VersionFrozen(ConflictError): code = "version.frozen"`; create `services/api/db_errors.py` `map_integrity_error(exc) -> AppError | None` per contract api §1. Focused: T034
- [ ] T039 [US4] Green: apply `map_integrity_error` at the write sites — `create_version` and the publish update in `services/api/routers/agents.py`, the promote in `services/api/routers/evals.py`, agent create (`agents.py` ~L148); in the knowledge-base create route (`services/api/routers/knowledge.py`) raise `InputInvalid` when `embedding_dim != 1024` (router-level, so the envelope is 400 not FastAPI's 422); in `services/api/ingest_repo.py` catch `chunk_dim_mismatch` on the chunk insert and fail the job with `error={"type":"chunk_dim_mismatch","retryable":false}`. Unmapped errors re-raise. Focused: T035
- [ ] T040 [US4] Regression: run `pytest -q -m integration` and fix only **fixtures/helpers that seed now-invalid data** (e.g. `services/tests/_api_helpers.py`, snapshot/hash fixtures creating tool bindings with no target or bindings after version commit) so they seed valid data; never change an assertion's meaning. List every fixture changed and why in the Evidence section.

**Checkpoint US4**: quickstart §3 table all pass; integration suite green.

---

## Phase 7: Polish & Cross-Cutting

- [ ] T041 [P] Red: in `services/tests/test_api_prometheus.py` assert the five series exist with exactly these label sets — `ac_provider_retries_total{provider,model,class}`, `ac_breaker_transitions_total{provider,model,class,to}`, `ac_breaker_refusals_total{provider,model,class}`, `ac_breaker_degraded_total{}`, `ac_batch_queue_wait_seconds{provider,model}` — and that driving the observer events increments them; no label carries a workspace/agent/run id (FR-013). Focused: `pytest -q tests/test_api_prometheus.py -k breaker or retries or batch`
- [ ] T042 Green: add the series to `services/api/metrics.py` and the observer in `services/api/resilience_wiring.py` mapping contract §7 events → series (observer exceptions swallowed). Focused: T041
- [ ] T043 [P] Docs: add the **Provider circuit breaker (025)** row to the per-control table in `docs/backend/degraded-mode.md` (contract api §6) with real code/test links; note that an alert *rule* on `ac_breaker_degraded_total` is deferred to the first production deploy (no alerting stack in repo — constitution §8).
- [ ] T044 [P] Docs: as-built resilience note in `docs/backend/02-provider-gateway.md` (retry-before-first-event, Retry-After cap, breaker keys/classes, G1 hidden-retry removal, kill switches `AC_PROVIDER_MAX_ATTEMPTS=1`, FR-015 cost basis = reported usage only) and a one-line pointer in `docs/backend/03-runtime.md`; integrity note (0015, F15 parity, composite FKs) in `docs/backend/00-overview.md` or the data-model doc it references.
- [ ] T045 Regenerate contracts: `(cd .. && services/.venv/bin/python scripts/emit_openapi.py)` then `cd frontend && source ~/.nvm/nvm.sh; nvm use 24; npm run gen:api && npm run lint && npm run build`; confirm `retries` on `RunOut` in `frontend/src/lib/api/schema.ts`.
- [ ] T046 Full regression (SC-006): `pytest -q -m "not integration"`, `pytest -q -m integration`, `make test-integration`, `pytest -q tests/test_runtime_purity.py`; record counts vs T001 baseline in Evidence. Any pre-existing expected-fail (`test_heldout_gate_sc004`) stays as-is and is reported.
- [ ] T047 Walk `specs/025-platform-hardening/quickstart.md` §1–§6 end to end and record results (including the "Not verified" list) in Evidence.
- [ ] T048 DoD reconcile (standing rule): `docs/implementation-plan.md` M0/M1 rows + audit note (gaps 1–2 closed, what remains), `docs/backend-integration-status.md`, a `docs/scope.md` decision-log row (025 closed at Must tier; items 8–13 deferred), `CLAUDE.md` "Current state" audit bullet, and project memory. Do **not** commit — commit only on owner request, never to `main`.

---

## Dependencies & Execution Order

```
Phase 1 Setup ──▶ Phase 2 Foundational (T003–T008)
                     │
        ┌────────────┼──────────────────────────────┐
        ▼            │                              ▼
   US1 (T009–T016)   │                       US4 (T032–T040)   ← independent; parallel track
        ▼            │
   US2 (T017–T026)   │   (needs US1 attempt loop + Guard)
        ▼            │
   US3 (T027–T031)   │   (needs US2 breaker/admission)
        └────────────┴──────────────▶ Phase 7 Polish (T041–T048)
```
- Within each story: all Red tasks → owner confirms Red evidence → Green tasks in listed order (same-file Greens are sequential: T013→T022→T030 all touch `resilience.py`; T015/T024 both touch `invoke.py`).
- T016 (migration 0014) must land before T036 (0015 `down_revision = "0014"`).

## Parallel Opportunities
- Phase 2: T003, T004, T005 (same file → write together, one Red run) ∥ T007.
- US1 Red: T009 ∥ T010 ∥ T011 ∥ T012.
- US2 Red: T017 ∥ T018 ∥ T019 ∥ T020 ∥ T021.
- US3 Red: T027 ∥ T028 ∥ T029.
- US4 Red: T032 ∥ T033 ∥ T034 ∥ T035; and the whole US4 track ∥ US1–US3 after Phase 2.
- Polish: T043 ∥ T044 ∥ T041.

Example (US1): write T009–T012 in one pass → run each focused command → show the four Red outputs to the owner →
on confirmation implement T013 → T014 → T015 → T016, re-running the focused + regression commands after each.

## Implementation Strategy
1. **MVP = Phase 2 + US1**: removes the hidden SDK retries (G1) and makes transient hiccups invisible to users, with
   honest `retries` on every run. Shippable alone (breaker absent ⇒ bounded 3 attempts, still deadline-safe).
2. **+US4** (parallel track): closes the reproducibility gap; independent migration 0015.
3. **+US2 → +US3**: breaker, then bulkhead, each an independently testable increment.
4. **Polish**: metrics, docs, regen, full regression, DoD reconcile.

## Evidence (filled during implementation)
- T001 baseline (2026-11-15): 1103 collected; `-m "not integration"` 739 passed / 1 skipped / 1 failed (pre-existing
  `test_injection_scanner.py::test_heldout_gate_sc004`, owner-gated held-out corpus from 024 — not a regression);
  `-m integration` 358 passed / 4 skipped (compose stack up); `alembic current` = `0013 (head)` (needs `AC_ENV=dev`).
- T002: `tests/_fault_transport.py` smoke-checked (fail→partial→ok, last step repeats, call counter); collection 1155.
- Red runs (per task):
  - T003–T005 `pytest -q tests/test_transport.py`: 18 failed / 13 passed. Failures: `KeyError: 'max_retries'` (stream,
    embed — G1 hidden retries confirmed), `AttributeError: parse_retry_after` ×12, `AttributeError: retry_after` ×3,
    `[409-False]` → `AssertionError: assert True is False`. `test_stream_retry_kwargs_are_setdefault_not_overwrite`
    passes pre-Green (regression guard, not Red).
  - T007 `pytest -q tests/test_resilience_types.py`: 27 failed (all `AttributeError` on missing types) after a
    behaviour-free docstring stub `runtime/providers/resilience.py` (§9 scaffolding, avoids ImportError-as-Red);
    `test_runtime_purity.py` still green.
- Green (owner accepted Red as-is, 2026-11-15):
  - T006 transport: `pytest -q tests/test_transport.py tests/test_gateway.py tests/test_gateway_embed.py` → 65 passed.
  - T008 resilience types: `+ tests/test_resilience_types.py tests/test_runtime_purity.py` → 93 passed (foundational green).
- US1 Red (2026-11-15, awaiting owner review before T013–T016):
  - T009 `tests/test_resilience_retry.py`: 22 failed / 1 passed — 21× `AttributeError: …resilience has no attribute 'Guard'`,
    1× `… 'default_guard'`. S4 reasoning/tool-call cases use an adapter-level stub (OpenAI chunk mapper only emits text).
    Deadline for S6 = the gateway's existing `timeout` arg (invoke already passes the run deadline).
  - T010 `tests/test_gateway_embed.py -k attempt`: 7 failed — 6× no `Guard`, 1× `EmbeddingError` has no `attempts`.
  - T011 `tests/test_invoke_resilience.py`: 7 failed — 6× no `Guard`, 1× `RunContext` has no `guard`. Real gateway +
    adapter, transport faked; pre-Red probe confirmed clean→SUCCESS, partial→PROVIDER_ERROR today.
  - T012 `-m integration tests/test_api_runs_retries.py`: 4 failed — column missing (0014), `Run.retry_count` missing,
    `KeyError: 'retries'` (OpenAPI), and fail×2-then-success playground run persists `provider_error` (no retry today).
  - Fast suite with US1 Red present: 37 failed (36 new Red + pre-existing `test_heldout_gate_sc004`) / 792 passed / 1 skipped — no regression.
  - Known Green impact: invoke-level test helpers that stub `gateway.stream`/`gateway.embed` with fixed signatures
    (`make_gateway`, `make_multi_turn_gateway`, ad-hoc fakes) must accept `**_` once invoke passes `guard=`/`traffic_class=`
    (helper signature only; no assertion changes). The process-default guard needs a per-test reset fixture (conftest)
    so retry sleeps don't cost wall time and breaker state (US2) doesn't leak across tests.
- US1 Green (owner "go", 2026-11-15) — T013–T016:
  - Focused: `test_resilience_retry.py` 23 passed; `test_gateway_embed.py` (incl. 7 `attempt_*`) + gateway/transport/types/purity
    regression 126 passed; `test_invoke_resilience.py` 7 passed; `-m integration test_api_runs_retries.py` 4 passed.
  - Migration 0014 applied to local DB (`alembic upgrade head` from `services/`, now `0014 (head)`).
  - Full: fast 828 passed / 1 skipped / 1 failed (pre-existing `test_heldout_gate_sc004`, owner-gated corpus);
    integration 362 passed / 4 skipped / 0 failed.
  - Harness-only changes (no assertion meaning changed):
    - `**_resilience` appended to fixed-signature gateway stubs in `_evals_helpers.py`, `_invoke_helpers.py`,
      `test_api_provider_key_resolve.py`, `test_call_record_emission.py`, `test_evals_judge.py`, `test_evals_runner.py`,
      `test_invoke_deadline.py`, `test_invoke_parity.py`, `test_invoke_retrieval.py`, `test_invoke_retrieval_scan.py`,
      `test_retrieval_quality.py`, `test_retrieval_step.py`, `test_sli_metrics.py`.
    - `conftest.py`: autouse `_fresh_default_guard` (reset default guard per test, instant sleep).
    - `test_migration_0013.py`: final assert `"0013"` → `"(head)"` (stale hardcoded head; intent = "back at head").
    - `test_api_ingestion.py`: `EmbedStub.fail_repeat`; the retryable-failure test now fails all 3 gateway attempts so the
      first job still ends `embedding_failed`/retryable (one transient 503 is now absorbed by the retry — intended 025 behaviour).
- US2 Red (2026-11-15, awaiting owner review before T022–T026):
  - Shared scenarios `tests/_breaker_scenarios.py` (B1–B8 + trial-lease expiry) run against BOTH stores.
  - T017 `tests/test_resilience_breaker.py`: 30 failed / 2 passed — all `AttributeError: …resilience has no attribute
    'LocalBreakerStore'` (or `guard.store is None`). The 2 passes are the S10 guards (config errors raise before
    admission; trivially true until admission exists — they pin it stays true).
  - T018 `tests/test_breaker_store.py`: 17 failed — `ModuleNotFoundError: api.breaker_store` (15), `api.resilience_wiring` (2).
  - T019 `test_invoke_resilience.py -k unavailable`: 3 failed (`guard.store` None → no `record_failure`);
    `test_ingest_pipeline.py::test_unavailable_…`: 1 failed (`'embedding_failed' == 'provider_unavailable'`);
    `test_ingest_executor_unit.py::test_unavailable_…`: 1 failed (no `api.resilience_wiring`).
    Deviation: the job-error mapping test lives in `test_ingest_pipeline.py` (where the pipeline fakes are) and uses the
    existing job-error shape `{"code","message","retryable"}` → `code == "provider_unavailable"` (contract api §1 wording),
    not `{"type": …}` as T019's text says; `test_ingest_executor_unit.py` pins the wiring (`traffic_class="batch"`, process guard).
  - T020 `-m integration tests/test_api_invoke_breaker.py`: 1 error at setup (no `api.resilience_wiring`). Deviation: invoke is
    SSE-only (`_run_invoke` always returns `StreamingResponse`), so the non-streaming `502 provider.unavailable` case has no
    reachable route; the test pins the terminal SSE `error` frame `code == "provider_unavailable"`, `done.status`, the persisted
    outcome/cost/retry_count, and `ac_runs_total{status="provider_unavailable"}`.
  - T021 `test_settings.py`: 14 failed — no settings fields (1), no `api.resilience_wiring.policy_from_settings` (1),
    `≤ 0` not rejected (12).
  - Design pins introduced by the Red (owner to confirm): admission once per call (not per retry attempt); a failed half-open
    trial is NOT retried (re-opens immediately); constructors `LocalBreakerStore(failure_threshold, cooldown_s, clock)`,
    `RedisBreakerStore(client, failure_threshold, cooldown_s)`; settings field names `provider_max_attempts` … `batch_concurrency`.
  - Fast suite with US2 Red present: 67 failed (66 new Red + pre-existing `test_heldout_gate_sc004`) / 830 passed / 1 skipped.
- US2 Green (owner "go ahead with recommendations", 2026-11-15 — 502 dropped as deviation; trial not retried; admission
  once per call; ingest job error `code`) — T022–T026:
  - T022 `resilience.py`: `LocalBreakerStore` (D5 algorithm, fails window 10 min, trial lease) + `Guard.admit/record`
    (shared-store error ⇒ local store + `breaker_degraded`); gateway `stream`/`embed` admit before the first attempt
    (refusal ⇒ S9 ERROR / `EmbeddingError(provider_unavailable)`, 0 transport calls), record transient failures and
    successes per attempt. `record_failure` returns "opened" so `breaker_transition{to=open}` fires once.
  - T023 `api/breaker_store.py` `RedisBreakerStore` (EXISTS/GET/INCR/PEXPIRE/SET PX/SET NX PX/DEL + MULTI/EXEC, no Lua).
  - T024 `Outcome.PROVIDER_UNAVAILABLE`; invoke `_error_outcome` (both modes); retrieval binding
    `degraded_reason="provider_unavailable"`; ingest job error `provider_unavailable` (retryable).
  - T025 six `AC_*` settings (`gt=0`); `api/resilience_wiring.py` (`get_guard`, `policy_from_settings`, `reset_for_tests`,
    `_sleep` injection point); 4 resilience metrics + `record_resilience_event` observer; guard on invoke (both planes) +
    eval `RunContext` and ingestion `gateway.embed(traffic_class="batch")`. conftest resets the API guard per test (instant sleep).
  - T026 (verification, real compose Redis :6380, logical DB 14): 2 passed on first run.
  - Focused: breaker 32 + store/settings 61 passed; full fast 896 passed / 1 skipped / 1 failed (pre-existing
    `test_heldout_gate_sc004`); integration 363 passed / 4 skipped / 0 failed (+2 T026).
  - Harness-only change: `test_three_instances_one_trial_per_cooldown` trial step gets `delay_s=0.05` — fakeredis never
    yields to the loop, so without it the trial finished and closed the breaker (valid B3) before the other instances
    admitted, and the test didn't exercise single-flight. Assertions unchanged.
- US3 Red (2026-11-15) — T027–T029: 7 Red for the intended reason, 13 verification green on first run.
  - Red (`test_resilience_bulkhead.py`): K1 20 batch → in-flight 20 (want 4); K1 per-(p,m) 8A+1B → 9 (want 5); K1 batch
    embeddings → 10 (want 4); K2 cap 1 + live → 4 (want 2); K3 queued eval run → SUCCESS (want TIMEOUT, 1 transport
    call); cancelled-waiter leak check → 3 (want 1); `ac_batch_queue_wait_seconds` → AttributeError.
  - Verification green (US1/US2 code, constitution §9): B6, B7, batch failures touch only `…:batch:fails`; T028 eval
    candidate+judge = batch even from a playground ctx; chat + query-embedding class per source (6 sources);
    T029 parity — one fault script through playground/api/channel/eval ⇒ identical retries/outcome/cost/tokens, both
    `invoke` and `invoke_result`, only the `retry` event's class differs. ⇒ T031 needs no code change unless Green reveals one.
  - Design pins added by the tests: `batch_wait` emitted on EVERY batch acquisition (zero waits included, so the histogram
    has honest percentiles), labels exactly {provider, model, seconds}; the semaphore is per Guard per (provider, model);
    a waiter cancelled while queued must not leak a slot; embeddings are capped per batch request.
- Fixtures changed in T040: _pending_
- T046 final counts: _pending_
- Not verified (carried from quickstart): real provider Retry-After shapes, real 429 bursts, threshold suitability, multi-replica beyond simulated instances.

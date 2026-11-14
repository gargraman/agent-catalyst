---
description: "Task list for 023 — Per-Call Model Log + End-to-End Correlation + Latency SLIs"
---

# Tasks: Per-Call Model Log + End-to-End Correlation + Latency SLIs (M6 remainder, slice 1)

**Input**: Design documents from `specs/023-m6-call-log-correlation/`
**Prerequisites**: plan.md, spec.md, research.md (D1–D12), data-model.md, contracts/observability.md, quickstart.md

**Tests**: INCLUDED — constitution principle 9 (test-first) and quickstart.md's RED→GREEN order. Every test task must be
written and observed FAILING before its implementation task.

**Organization**: by user story (US1 P1 per-call record · US2 P2 correlation · US3 P3 SLIs).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: parallelizable (different files, no dependency on an incomplete task)
- **[Story]**: US1 / US2 / US3 (user-story phases only)
- All paths are repo-relative. Backend root: `services/`. Run tests with `cd services && ./.venv/bin/python -m pytest`;
  integration tests need the local stack + the integration env from quickstart.md and `alembic upgrade head`.
- **No paid provider calls**: always replace the transport seam (`runtime.providers.transport.stream_chunks`) with a stub.

---

## Phase 1: Setup

- [X] T001 Record the green baseline before any change: run `./.venv/bin/python -m pytest -q -m "not integration"` and the full suite with the integration env in `services/`; note both pass counts in the PR/commit body (expected today: 483 infra-free, 729 full).
- [X] T002 [P] Spike (time-boxed, no paid calls): confirm with a stubbed `litellm.acompletion` capture that `extra_headers={"X-Correlation-Id": "…"}` passed to `litellm.acompletion` is forwarded for the `openai/…` and `anthropic/…` model ids, and whether litellm exposes provider **response** request ids on the streamed response (e.g. `_hidden_params` / response headers). Check current provider docs for a client-supplied request-id header (research D7 believes OpenAI accepts `X-Client-Request-Id` — unverified). Record findings in `specs/023-m6-call-log-correlation/research.md` D7 and fill the TBD cells of the provider table in `specs/023-m6-call-log-correlation/contracts/observability.md` §3.

---

## Phase 2: Foundational (blocks all stories)

- [X] T003 Add the frozen value object `ModelCallRecord` to `services/runtime/run.py` with exactly these fields (data-model.md): `run_id: str`, `correlation_id: str`, `provider_correlation_id: str` ("platform-generated 32-hex id sent to the provider; never the caller's value" — FR-014), `workspace_id: str`, `agent_id: str`, `version_id: str`, `source: str` ("playground | api | eval"), `role: str` (`"judge"` if `version_id == "judge"` else `"candidate"`), `call_seq: int` ("0-based turn index within the run"), `correction: bool`, `provider: str`, `model: str`, `prompt_version: str` (snapshot `prompt_hash`), `input_text: str`, `output_text: str` ("raw, in-process only"), `latency_ms: int`, `first_token_ms: int | None`, `cost_usd: Decimal` ("the gateway-attached turn cost; `0` when no usage"), `cost_known: bool`, `success: bool`, `error_code: str | None` ("normalized error code (no raw provider payload)"), `provider_request_id: str | None = None`. Also add `first_token_ms: int | None = None` to `RunRecord`. Pure — no new imports beyond dataclasses/Decimal.
- [X] T004 Extend `RunContext` in `services/runtime/context.py`: `record_call: CallSink | None = None` where `CallSink = Callable[[ModelCallRecord], None | Awaitable[None]]`, `content_logging: bool = False` (carried for the sink; unused by runtime logic), and `provider_correlation_id: str | None = None` (set by the API edge; FR-014). Keep defaults so every existing constructor call stays valid.
- [X] T005 Run `./.venv/bin/python -m pytest -q -m "not integration"` in `services/` — must remain fully green (fields are additive with defaults); `tests/test_runtime_purity.py` must pass.

**Checkpoint**: value objects exist; nothing emits yet.

---

## Phase 3: User Story 1 — Every model call leaves one auditable record (Priority: P1) 🎯 MVP

**Goal**: one per-call record per model call from the shared runtime path, written as one JSON line by an API sink;
metadata by default, redacted text only on admin opt-in; per-call costs sum exactly to the run cost.

**Independent Test**: playground + published + eval (incl. judge) runs against a stub provider each produce exactly one
line per model call with an identical key set; Σ line `cost_usd` == the run's `runs.cost_usd`; default lines contain
no `input_text`/`output_text`.

### Tests for User Story 1 (write first, confirm RED)

- [X] T006 [P] [US1] Create `services/tests/test_call_record_emission.py` (pure, stub transport, `invoke`/`invoke_result` with a `RunContext(record_call=list.append)`): exactly one `ModelCallRecord` per gateway turn for (a) single-turn success, (b) validated-mode correction turns (`call_seq` 0..n, `correction` true for turn > 0), (c) provider ERROR → `success=False`, `error_code` set, (d) deadline/timeout → `success=False` with partial `latency_ms`, (e) consumer closes the `invoke()` generator mid-stream → one record with `success=False`; (f) pre-call rejections (invalid input, config_error/unknown model) → **zero** records; (g) a `record_call` that raises does not change the run's `RunRecord`, outcome, or streamed events; (h) judge snapshot (`version_id="judge"`) → `role == "judge"`; (i) **cost invariant**: `sum(r.cost_usd for r in records) == run.cost_usd` exactly (Decimal) for single-turn, correction-turn and failed-turn runs; a turn without USAGE → `cost_usd == 0` and `cost_known is False`.
- [X] T007 [P] [US1] Create `services/tests/test_redact.py` (pure) for `runtime.guardrails.redact.redact(text, known_secrets=())`: exact-match scrub of every `known_secrets` value (even when it matches no pattern); each pattern → `[REDACTED:<kind>]` — `sk-…`, `sk-ant-…`, AWS `AKIA/ASIA…`, GitHub `gh[pousr]_…`, Slack `xox[abprs]-…`, JWT (3 base64url segments), PEM private-key block, `Bearer <token>`, email, phone (E.164 + common national), Luhn-valid card (and a Luhn-invalid 16-digit number NOT redacted), US SSN shape; empty/None → `""`; deterministic output; plain prose unchanged.
- [X] T008 [P] [US1] Create `services/tests/test_call_log_sink.py` (pure) for `api.call_log.make_call_sink(...)` with a captured `ac.call_log` handler: content OFF → the line's key set equals exactly contract §1 "Always present" (no `input_text`/`output_text`); `cost_usd` is `str(Decimal)` of the record; `input_fp`/`output_fp` are 32 hex chars, stable for the same text+workspace, **different** across two workspaces, `""` for empty text; content ON → `input_text`/`output_text` redacted then truncated to **2,048** chars (a secret straddling the cut must not survive), redactor exception → literal `"[redaction-failed]"`; the run's provider api key never appears anywhere in the serialized line (OFF and ON, incl. inside `error_code`); a failure injected at each stage (`fingerprint|redact|serialize|write`) never raises out of the sink and increments `ac_call_log_errors_total{stage}`; exactly one line per record, one JSON object per line.
- [X] T009 [P] [US1] Extend `services/tests/test_settings.py`: `AC_ENV=prod` without `AC_TELEMETRY_FINGERPRINT_KEY` fails settings load (fail closed); non-prod gets a fixed dev default; `AC_TELEMETRY_LOG_CONTENT_IN_DEV` defaults to `True` and is ignored when `AC_ENV=prod`.
- [X] T010 [P] [US1] Create `services/tests/test_api_telemetry_setting.py` (`@pytest.mark.integration`): `GET /api/v1/workspace/telemetry` → `{"content_logging": false, "effective": <dev override>}` with no settings row; admin `PUT {"content_logging": true}` → 200 and persisted; builder/publisher/viewer `PUT` → 403 `access.denied` and no row change; unknown body key → 422 (repo convention for body-schema errors); a value change emits exactly one audit line `event=telemetry.content_logging.changed` with `subject, workspace_id, old, new, correlation_id, ts`; a same-value PUT emits **no** audit line; cross-workspace isolation (setting in ws A doesn't affect ws B).
- [X] T011 [US1] Create `services/tests/test_api_call_log.py` (`@pytest.mark.integration`, stub transport, `AC_TELEMETRY_LOG_CONTENT_IN_DEV=false`): playground invoke, published `/v1` invoke, and an eval run with ≥2 cases (candidate + judge calls) each emit exactly one line per model call; all lines share an identical key set across the 4 caller types; judge lines have `role="judge"`; Σ line `cost_usd` == the persisted `runs.cost_usd` for each candidate run; with defaults no line contains `input_text`/`output_text` (SC-008); after admin enables `content_logging`, lines for that workspace contain redacted text and a seeded provider key / `sk-ant-…` / email in the prompt are absent raw (SC-004).

### Implementation for User Story 1

- [X] T012 [P] [US1] Create `services/runtime/guardrails/__init__.py` and `services/runtime/guardrails/redact.py` implementing `redact(text: str | None, *, known_secrets: Iterable[str] = ()) -> str` per research D5 (exact-match first, then credential patterns, then PII patterns; `[REDACTED:<kind>]`; pure, stdlib `re` only). Module docstring: the shared primitive the future output guardrail must reuse (FR-010). Makes T007 green.
- [X] T013 [US1] Emit per-turn records in `services/runtime/invoke.py`: in `_pipeline`, wrap each gateway turn (pass-through and `_collect_turn`/correction turns) in `try/finally` that builds one `ModelCallRecord` (fields per T003; `input_text` = the turn's rendered user/messages text, `output_text` = turn text; `cost_usd`/`cost_known` from the turn's `usage`; `success` true only on terminal stop with usage and no error; `role` from `snapshot.version_id == "judge"`) and calls a new `_emit_call(ctx, rec)` that awaits awaitables and swallows **all** exceptions (FR-009). No emission when no provider call was made. `first_token_ms` stays `None` here (US3). Makes T006 green.
- [X] T014 [P] [US1] Add settings in `services/api/settings.py`: `telemetry_fingerprint_key: SecretStr` (alias `AC_TELEMETRY_FINGERPRINT_KEY`, fixed dev default; model validator raises when `env == "prod"` and the value is the dev default or empty) and `telemetry_log_content_in_dev: bool = True` (alias `AC_TELEMETRY_LOG_CONTENT_IN_DEV`). Makes T009 green.
- [X] T015 [US1] Create `services/api/call_log.py`: dedicated logger `ac.call_log` with its own `StreamHandler(sys.stdout)`, raw-message formatter, level INFO, `propagate=False` (research D9); `fingerprint(workspace_id, text) -> str` = `HMAC-SHA256(HMAC-SHA256(root_key, workspace_id), text)` hex truncated to **32** chars, `""` for empty text (D4); `make_call_sink(*, workspace_id, api_key, content_logging) -> CallSink` producing the contract §1 line (`event="model_call"`, RFC 3339 UTC `ts`, `cost_usd=str(Decimal)`, `input_chars/output_chars`, `input_fp/output_fp`, and only when `content_logging` is effective `input_text/output_text` = `redact(text, known_secrets=[api_key])[:2048]` or `"[redaction-failed]"`); every stage wrapped so failures increment `ac_call_log_errors_total{stage}` and never raise. Add `ac_call_log_errors_total{stage}` Counter to `services/api/metrics.py` with a best-effort `record_call_log_error(stage)`. Makes T008 green.
- [X] T016 [P] [US1] Create migration `services/migrations/versions/0012_workspace_content_logging.py` (`revision="0012"`, `down_revision="0011"`): `ALTER TABLE workspace_settings ADD COLUMN content_logging boolean NOT NULL DEFAULT false`; downgrade drops it. Add `content_logging: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())` to `WorkspaceSettings` in `services/api/models.py`.
- [X] T017 [P] [US1] Add `TelemetrySettingsIn` (`content_logging: bool`, `model_config = ConfigDict(extra="forbid")`) and `TelemetrySettingsOut` (`content_logging: bool`, `effective: bool`) to `services/api/schemas/workspaces.py`.
- [X] T018 [US1] In `services/api/call_log.py` add `async def effective_content_logging(session, workspace_id) -> tuple[bool, bool]` returning `(stored, effective)`: stored = `workspace_settings.content_logging` (missing row ⇒ `False`); effective = `stored or (settings.env != "prod" and settings.telemetry_log_content_in_dev)`.
- [X] T019 [US1] Add `GET /api/v1/workspace/telemetry` (`require_permission("view")`) and `PUT /api/v1/workspace/telemetry` (`require_permission("settings.manage")`, `rate_limit("default")`) to `services/api/routers/workspaces.py`; PUT upserts the settings row (create lazily like `patch_settings` first-write) and, only when the value changes, writes one structured audit line via the module logger: `event=telemetry.content_logging.changed`, `subject`, `workspace_id`, `old`, `new`, `correlation_id` (use the `correlation_id` dependency), `ts`. Makes T010 green; confirm the 008 deny-by-default RBAC coverage test still passes.
- [X] T020 [US1] Wire the sink in `services/api/routers/invoke.py` (`_run_invoke`, used by both console and published planes): after resolving the provider key, compute `(_, effective) = await effective_content_logging(session, ws)` and set `ctx.record_call = make_call_sink(workspace_id=ws, api_key=api_key, content_logging=effective)` and `ctx.content_logging = effective`.
- [X] T021 [US1] Wire the same in `services/api/routers/evals.py` `create_eval_run` (the judge reuses this ctx, so judge calls are covered). Makes T011 green.
- [X] T022 [US1] Run the full suite with the integration env in `services/` (after `alembic upgrade head` for 0012); all green incl. `tests/test_runtime_purity.py` and `tests/test_invoke_parity.py` (parity must still hold — the sink is per-ctx, not per-path).

**Checkpoint**: US1 is shippable alone (MVP): A2 satisfied as metadata-by-default with audited opt-in.

---

## Phase 4: User Story 2 — One correlation id follows a request to the provider (Priority: P2)

**Goal**: the edge-resolved correlation id is on every call line; every outbound provider request carries only a
**platform-generated** provider correlation id (U1 / FR-014), also recorded on every call line.

**Independent Test**: a request with `X-Correlation-Id: demo-123` → `demo-123` on the run row and every call line,
**absent** from captured outbound kwargs, which carry the lines' `provider_correlation_id` (32 hex); without an inbound
id, one generated id is used for the row, lines, and outbound.

### Tests for User Story 2 (write first, confirm RED)

- [X] T023 [P] [US2] Create `services/tests/test_outbound_correlation.py` (pure, capture `transport.stream_chunks` kwargs): (a) `invoke` with `ctx.correlation_id="demo-123"`, `ctx.provider_correlation_id=<32-hex P>` → every transport call receives `extra_headers == {"X-Correlation-Id": P}` (plus any provider-native header confirmed in T002) and `"demo-123"` appears in **no** transport kwarg; (b) `ctx.correlation_id=None, provider_correlation_id=None` → runtime mints one id and uses it for RunRecord, every record's `correlation_id` **and** `provider_correlation_id`, and the outbound header; (c) `ctx.correlation_id="demo-123", provider_correlation_id=None` → a fresh 32-hex id ≠ `demo-123` is minted and used outbound; (d) a non-32-hex `provider_correlation_id` (e.g. `"demo-123"`, 200 chars, contains a newline) → header omitted, call still made, no error; correction turns carry the same id; `ModelCallRecord.provider_correlation_id` equals the outbound value on every record; if T002 confirmed response-id capture, `provider_request_id` is populated from the stubbed response, else stays `None`. Also extend `services/tests/test_correlation.py`: `resolve_correlation(request)` returns `(id, supplied)` — `supplied` True for a valid traceparent / X-Correlation-Id, False when generated; `provider_correlation_id_for(id, supplied)` returns `id` when not supplied, else a fresh 32-hex value ≠ id.
- [X] T024 [P] [US2] Extend `services/tests/test_api_call_log.py` (`@pytest.mark.integration`) with an end-to-end correlation case: inbound `X-Correlation-Id: demo-123` → `runs.correlation_id` and every call line's `correlation_id` are `demo-123`, every captured outbound `extra_headers["X-Correlation-Id"]` equals the lines' `provider_correlation_id` (32 hex, ≠ `demo-123`), and `demo-123` appears in no outbound kwarg; same with a valid inbound `traceparent`; no inbound id → the response's echoed `X-Correlation-Id` equals the id on the run row, all lines' `correlation_id` and `provider_correlation_id`, and all outbound calls; an eval run with N cases → all candidate **and** judge lines share one `correlation_id` and one `provider_correlation_id` (FR-013).

### Implementation for User Story 2

- [X] T025 [P] [US2] Add `provider_correlation_id: str | None = None` to `NormalizedRequest` in `services/runtime/providers/ir.py` (not part of any hash; snapshot/prompt hashing untouched).
- [X] T026 [US2] In `services/runtime/invoke.py`, resolve the run's provider correlation id once — `ctx.provider_correlation_id` if set; else the runtime-minted correlation id when `ctx.correlation_id` was `None`; else a fresh `uuid.uuid4().hex` (never copy a caller value) — and build every `NormalizedRequest` (initial and correction turns) with `provider_correlation_id=<it>`; put the same value on every `ModelCallRecord`. In `services/api/correlation.py` add `resolve_correlation(request) -> tuple[str, bool]` (keep `resolve_correlation_id` as a thin wrapper so 010 behavior is unchanged) and `provider_correlation_id_for(corr, supplied) -> str`; add a FastAPI dependency `correlation(request) -> Correlation(id, provider_id)` and use it in `services/api/routers/invoke.py` and `services/api/routers/evals.py` to set `RunContext.provider_correlation_id` (the response still echoes the caller-facing `correlation_id`).
- [X] T027 [US2] In `services/runtime/providers/_common.py` `stream_via_transport`, accept `provider_correlation_id: str | None = None`; only when it matches `^[0-9a-f]{32}$` add `kwargs["extra_headers"] = {"X-Correlation-Id": provider_correlation_id, **<provider-native header if T002 confirmed>}`, else omit silently. If T002 confirmed response request-id exposure, read it from the stream and include `provider_request_id` in the terminal USAGE event data (and the gateway's normalized ERROR path) so `_pipeline` can copy it onto the `ModelCallRecord`.
- [X] T028 [P] [US2] Pass `provider_correlation_id=req.provider_correlation_id` through in `services/runtime/providers/openai_adapter.py`, `anthropic_adapter.py`, and `ollama_adapter.py` `stream(...)` calls to `stream_via_transport`. Makes T023 green.
- [X] T029 [US2] Run the full suite with the integration env; T024 green; the existing 010 correlation tests (`tests/test_correlation.py`, `tests/test_api_correlation.py`) unchanged and green.

**Checkpoint**: one id retrieves run + call lines → provider correlation id → outbound requests (SC-003); no caller id ever leaves the platform (FR-014).

---

## Phase 5: User Story 3 — First-token latency and deadline cuts (Priority: P3)

**Goal**: headline SLIs on the existing `/metrics` surface, labeled by `source` only.

**Independent Test**: a stub with a 1.2 s first-token delay records one first-token observation in the ≤1.5 s bucket
for its source; one deadline-cut run increments `ac_deadline_exceeded_total` by exactly one for its source.

### Tests for User Story 3 (write first, confirm RED)

- [X] T030 [P] [US3] Create `services/tests/test_sli_metrics.py`: (a) runtime: pass-through run with a delayed first TEXT_DELTA → `RunRecord.first_token_ms` ≈ delay and the first turn's `ModelCallRecord.first_token_ms` set; validated-mode run → `RunRecord.first_token_ms is None`; (b) metrics via `metrics.record_run(rec)`: `ac_first_token_seconds{source}` observed once when `first_token_ms` is set and not otherwise; `ac_run_latency_seconds{source}` observed once per call; `ac_deadline_exceeded_total{source}` +1 **iff** `rec.outcome == Outcome.TIMEOUT` and equal to `ac_runs_total{status="timeout",source}` across a mixed batch of outcomes; histogram buckets exactly first-token `0.25,0.5,1,1.5,2,3,5,8,13,30` and run-latency `0.5,1,2,5,10,15,20,25,30,45`; (c) `GET /metrics` exposition contains no workspace/agent/user ids or prompt text (FR-018).

### Implementation for User Story 3

- [X] T031 [US3] In `services/runtime/invoke.py`, capture per-turn `first_token_ms` at the first TEXT_DELTA of each pass-through turn (monotonic clock from turn start), set it on that turn's `ModelCallRecord`, and set `RunRecord.first_token_ms` from the first turn; leave `None` in validated mode.
- [X] T032 [US3] In `services/api/metrics.py` add `ac_first_token_seconds` (Histogram, labels `source`, buckets `0.25,0.5,1,1.5,2,3,5,8,13,30`), `ac_run_latency_seconds` (Histogram, labels `source`, buckets `0.5,1,2,5,10,15,20,25,30,45`), `ac_deadline_exceeded_total` (Counter, labels `source`); change `record_run` to `record_run(rec)` (derive status/source from the RunRecord; keep it best-effort, never raising) observing all of them. Update the single caller in `services/api/run_sink.py` and adjust `services/tests/test_metrics_unit.py` to the new signature. Makes T030 green.
- [X] T033 [US3] Run the full suite with the integration env; `tests/test_api_prometheus.py` (010) still green.

**Checkpoint**: playground first-token p95 and deadline-cut rate computable per source (SC-006).

---

## Phase 6: Polish & Cross-Cutting

- [X] T034 [P] Reconcile `docs/backend/10-observability-monitoring.md` §1 and `docs/backend/lld/10-observability-monitoring.lld.md` §3/§5 to the built design: sink seam (runtime → `RunContext.record_call` → `api/call_log.py`), metadata-by-default with audited opt-in text (Q1 = A), keyed per-workspace fingerprints, redaction primitive location `runtime/guardrails/redact.py`.
- [X] T035 [P] Add a `docs/scope.md` decision-log row (2026-11-14): spec A2 "input, output" is satisfied as *identifiable by default (size + keyed fingerprint), readable on audited per-workspace opt-in* — data minimization (constitution 5); link feature 023.
- [X] T036 [P] Update `docs/implementation-plan.md` M6 DoD row (append the 023 slice: per-call log, outbound correlation, first-token/latency/deadline SLIs; remaining M6: budget reservation/TTL reaper, eval budget, Langfuse/ClickHouse, burn-rate alerts) and `docs/backend-integration-status.md` (new row: telemetry setting `GET/PUT /api/v1/workspace/telemetry` BUILT, FE MOCK; refresh test counts).
- [X] T037 Measure SC-007 per quickstart: 200 stub runs with vs without the sink, report median overhead (target ≤ 5%) in `specs/023-m6-call-log-correlation/quickstart.md` under an "Evidence" heading — reported, not a CI gate.
- [X] T038 Manual spot-check per quickstart.md against the running stack (`make up`, rebuild `api` + `migrate` images so 0012 applies): invoke with `X-Correlation-Id: demo-123`, confirm one `"event":"model_call"` line per call in `docker compose -f deploy/compose.yaml logs api`, no text by default, and the new series on `curl -s localhost:8000/metrics`. Record results under "Evidence" in `specs/023-m6-call-log-correlation/quickstart.md`.
- [X] T039 Final gate in `services/`: infra-free suite, full integration suite, and `tests/test_runtime_purity.py` all green; record before/after counts vs T001 in the commit body. Mark completed tasks `[X]` in this file.

---

## Dependencies & Execution Order

- **Setup (T001–T002)** → **Foundational (T003–T005)** → stories.
- **US1 (T006–T022)**: depends on Foundational. MVP.
- **US2 (T023–T029)**: depends on Foundational; its pure tests/impl (T023, T025–T028) are independent of US1, but its
  integration assertions on call lines (T024) need US1's sink (T015, T020, T021). T027's provider-native header and
  response-id parts depend on T002's findings.
- **US3 (T030–T033)**: depends on Foundational only (metrics hang off `record_run`, not the call sink); T031 touches
  `runtime/invoke.py`, so sequence it after T013/T026 to avoid edit conflicts.
- **Polish (T034–T039)**: after the stories it documents/verifies.

Within each story: tests (RED) → value objects/settings/migration → services → endpoints/wiring → suite run.

## Parallel Opportunities

- T002 alongside T001.
- US1 tests T006–T010 all in different files → write in parallel; then T012, T014, T016, T017 in parallel
  (redact module, settings, migration+model, schemas) before T013/T015/T018–T021.
- US2: T023 ∥ T024 (tests); T025 ∥ T028 after T027's signature is settled.
- US3 can proceed in parallel with US2 except the shared `runtime/invoke.py` edits (T026, T031) — serialize those.
- Docs T034–T036 in parallel.

### Example: US1 kickoff

```text
Parallel: T006 test_call_record_emission.py | T007 test_redact.py | T008 test_call_log_sink.py | T009 test_settings.py | T010 test_api_telemetry_setting.py
Then parallel: T012 redact.py | T014 settings.py | T016 0012 migration + model | T017 schemas
Then sequential: T013 invoke.py → T015 call_log.py → T018 → T019 → T020 → T021 → T022
```

## Implementation Strategy

1. **MVP = US1** (T001–T022): A2 contract met, metadata-by-default, audited opt-in, cost reconciliation. Shippable alone.
2. **+US2** (T023–T029): end-to-end correlation incl. the provider request.
3. **+US3** (T030–T033): latency/deadline SLIs.
4. **Polish** (T034–T039): docs reconciliation, overhead evidence, live spot-check, final gate.

Commit per story checkpoint on `feat/backend-m0-foundation` (Conventional Commits, `— TDD` suffix), only when asked.

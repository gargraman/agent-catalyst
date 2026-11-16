# Implementation Plan: Core Loop Live — build → test → evaluate → publish → call against the real backend

**Branch**: `feat/backend-m0-foundation` (spec dir `specs/026-core-loop-live`; the setup script reports
`026-core-loop-live` from the dir name, and no branch was created) | **Date**: 2026-11-16 | **Spec**: [spec.md](spec.md)
**Input**: `specs/026-core-loop-live/spec.md`. **Must tier only (scope items 1–9)**. US4-8 resolved as automatic
baseline + audited **admin** freeze (FR-023; owner answers O1–O5 recorded 2026-11-16). 025 is closed: binding integrity is enforced in
the DB.

## Summary

The core loop has two halves:

1. **Wire the built backend** to the console for configure / version / playground / publish / tokens / cURL. This is
   20 seam groups ([contracts/frontend-seams.md](contracts/frontend-seams.md)).
2. **Close four backend gaps** the research found are *not* just wiring (each named in [research.md](research.md)):
   - **G2/G3**: there is no draft at all. `create_version` clones the last version and silently drops tool/skill
     bindings.
   - **G10**: archived agents still answer on both planes.
   - **G11**: publish returns `advisory_verdict=None`.
   - **G12**: eval runs take the baseline, policy and cases from the request body and persist no aggregate. Any
     caller can manufacture a "pass".

Technical approach:
- A draft is stored as jsonb on the agent row under the existing `xmin` ETag (D1).
- Versions are cut from the draft, with bindings in the same transaction (D2).
- The playground can run the draft through a **transient** snapshot on the identical runtime path (D3).
- One additive migration `0016_core_loop` adds eval suites/cases/runs/results, a per-suite frozen-baseline pointer and
  a minimal append-only `audit_logs` (D6, D10).
- The eval-run body shrinks to `{version_id, suite_id}`, and baseline/policy are resolved server-side (D7, D8).
- On the FE, a `fetch`+`ReadableStream` SSE client handles streaming (D15) and a 7-way refusal table maps errors to
  messages (D16).
- Live checks run on a dev-only **fake provider transport**, so no paid calls are made (D22).

## Technical Context

**Language/Version**: Python 3.12 (services venv); TypeScript 5 / React 19 / Next.js 16.3.8 (modified, read
`frontend/node_modules/next/dist/docs/` before touching Next APIs)
**Primary Dependencies**: FastAPI, SQLAlchemy 2 async + asyncpg, Alembic, pydantic v2, existing `evals/` engine
(`drift.py`, `runner.py`, `judge.py`), litellm (transport only); FE: hand-rolled store on `useSyncExternalStore`,
`openapi-typescript` generated `schema.ts`. **No new dependencies.**
**Storage**: Postgres 16 + pgvector (compose host 5433), RLS FORCE on every new table; Redis (existing deny-set)
**Testing**: `pytest -m "not integration"` (infra-free: baseline resolution, case diff, reason codes, transient
snapshot, fake transport) + `pytest -m integration` (migration 0016, RLS/isolation, routes, audit grant); FE:
`npm run lint` + `npm run build` + the recorded live checks L1–L24 in [quickstart.md](quickstart.md)
**Target Platform**: Linux container API + Next.js console, local compose stack, single API replica (pilot)
**Project Type**: web service + web console (monorepo `services/` + `frontend/`)
**Performance Goals**: playground first token renders with **no added buffering** (FE parses frames incrementally);
eval run ≤ 25 cases inside the existing request deadline; history lists keyset-paginated
**Constraints**:
- `runtime/` stays pure (purity test).
- Playground, published invoke and eval keep **one** runtime path (parity test extended to the draft target).
- No paid provider calls in tests or the quickstart.
- Mock mode is behaviourally unchanged.
- Raw token secrets are never stored client-side or interpolated into snippets.
- No tenant content in audit rows, logs or metrics.

**Scale/Scope**:

| Area | Count |
|---|---|
| Migration | 1 (`0016`) |
| New backend modules | ~6 (`routers/eval_suites.py`, `eval_store.py`, `baseline.py`, `audit.py`, `drafts.py`, `providers/fake_transport.py`) |
| Touched backend modules | ~8 |
| New test files | ~11 |
| New FE modules | 2 (`api/stream.ts`, refusal map) |
| Touched FE files | ~8 (store, client, adapters, agent page, playground ×2, evaluations, live-status) |

## Constitution Check

*GATE: evaluated before Phase 0 and re-checked after Phase 1 design.*

| # | Principle | How this plan complies | Gate |
|---|-----------|------------------------|------|
| 1 | Verifiable customer outcome | Each US maps to named tests + live checks L1–L24. Unsupported FE assertion kinds are **disabled** in live mode, not stored-and-ignored (D6). Duplicate/delete say "not available yet" rather than fake success | ✅ |
| 2 | Scope & phases | Must items 1–9 only. Items 10–15 out. The owner's release plan names M12 a v1 blocker. Deviations from design docs (baseline keyed per suite; freeze perm = publish) are recorded as deltas at DoD, plus a `docs/scope.md` row | ✅ |
| 3 | Deterministic controls, fail-closed | Baseline and policy are server-resolved; client-sent baseline/policy/cases are dropped unread (`extra="ignore"`, owner O3) — closes G12. Archive is enforced in the invoke path (closes G10). Permissions are checked in deps before lookup. The fake transport is refused under `AC_ENV=prod` | ✅ |
| 4 | Evidence about agent behaviour | Eval runs are persisted with the resolved policy, judge version, baseline provenance and case diff. **No silent rebaseline**: the automatic baseline is named on every verdict, and a freeze is explicit and audited. The publish verdict stays **advisory**, because the judge is uncalibrated and §4 forbids it as a gate | ✅ |
| 5 | Tenant boundaries | RLS FORCE + composite `(workspace_id, id)` FKs on all new tables (025 lesson G8). Denial = 404. Isolation tests per table. Audit `meta` holds ids only | ✅ |
| 6 | Reproducible versions | Versions are cut from the draft with bindings in one transaction (fixes the G3 binding drop). Evals run only on immutable versions; a draft is playground-only. Re-point is audited | ✅ |
| 7 | Honest providers & cost | Turn cost comes from the `usage` frame (`cost_is_estimate` shown). Fake-transport costs are marked estimates. Cost on a failed eval run stays on its per-case runs | ✅ |
| 8 | Operate; failures visible | Seven distinct refusal messages. Interrupted streams are marked incomplete. Eval `failed` status has a reason. No silent mock fallback (FR-019) | ✅ |
| 9 | Test-first | Every behaviour-changing backend task: Red test shown to the owner before Green (tasks.md names the file + command). **FE exception, disclosed**: no FE harness exists, so FE evidence = lint + build + recorded live checks. `parseSseChunk` is kept pure so it is testable later. **Approved 2026-11-16** (owner: "take recommended options where decisions are not clear" at /speckit-tasks) | ✅ (exception approved) |
| — | Min. verification: API/frontend | typed contracts regenerated; streaming contract; FE build | ✅ |
| — | Min. verification: Database/tenant | migration round-trip, RLS isolation, negative cross-tenant, append-only grant | ✅ |
| — | Min. verification: Model/prompt | eval persistence uses the existing engine. No judge or prompt change. A frozen baseline now exists server-side | ✅ |
| — | Paid calls | tests/quickstart none (D22). **One** owner-approved smoke (O5): `claude-haiku-5-5`, worst-case preflight, workspace cap $0.05, evidence retained | ✅ |

**Result (pre-Phase 0)**: PASS, with one disclosed exception (FE test-first, §9), approved 2026-11-16.
**Re-check (post-Phase 1)**: PASS. The design added the audit table and the fake transport. Both reduce risk (an
auditable freeze; no paid tests). The fake transport is the only new prod-reachable config and is refused under prod.

## Project Structure

### Documentation (this feature)

```text
specs/026-core-loop-live/
├── spec.md
├── plan.md                    # this file
├── research.md                # G1–G25 ground truth, D1–D22 decisions, O1–O5 owner answers
├── data-model.md              # migration 0016: agents/agent_versions deltas, eval_*, audit_logs, state machines
├── quickstart.md              # backend tests + live checks L1–L24 (fake transport, no paid calls)
├── contracts/
│   ├── api.md                 # route deltas A–G + eval behavioural table E1–E8
│   └── frontend-seams.md      # S1–S20 seam groups, counting rule, states
├── checklists/requirements.md
└── tasks.md                   # Phase 2 (/speckit-tasks; not created here)
```

### Source Code (repository root)

```text
services/
├── api/
│   ├── models.py                       # EDIT Agent.description/draft/status_before_archive; AgentVersion.created_by; Eval*; AuditLog
│   ├── drafts.py                       # NEW  Draft schema, normalize(), resolve(agent, active), dirty(), transient_version()
│   ├── baseline.py                     # NEW  resolve_baseline(), case_diff(), reason codes (pure over rows; infra-free tests)
│   ├── eval_store.py                   # NEW  persist completed/failed runs + results (one txn)
│   ├── audit.py                        # NEW  record(session, principal, action, target_type, target_id, meta)
│   ├── errors.py                       # EDIT AgentArchived (409 agent.archived), EvalSuiteHasRuns (409)
│   ├── settings.py                     # EDIT AC_PROVIDER_TRANSPORT (litellm|fake; fake refused in prod)
│   ├── schemas/{agents,invoke,evals}.py   # EDIT per contracts/api.md (+ eval_suites schemas)
│   └── routers/
│       ├── agents.py                   # EDIT PATCH description/draft/archive-restore; create_version from draft; audit
│       ├── invoke.py                   # EDIT target=draft|version|active; archived refusal (409 console / 404 published)
│       ├── evals.py                    # EDIT eval-runs body {version_id, suite_id}; history routes; publish advisory + audit
│       └── eval_suites.py              # NEW  suites/cases CRUD + baseline PUT/DELETE
├── runtime/providers/fake_transport.py # NEW  deterministic scripted transport (pure; no I/O) — wired only from api/
├── migrations/versions/0016_core_loop.py   # NEW
└── tests/                              # NEW files listed in quickstart §1; EDIT test_api_evals*.py (body shape), test_invoke_parity.py (+draft)
deploy/compose.yaml                     # EDIT api env AC_PROVIDER_TRANSPORT passthrough
frontend/src/
├── lib/api/client.ts                   # EDIT export typed calls (patch/versions/publish/tokens/suites/cases/baseline/eval-runs); shared parseError; ETag capture
├── lib/api/stream.ts                   # NEW  streamInvoke + pure parseSseChunk
├── lib/api/adapters.ts                 # EDIT AgentOut (rich), VersionOut, TokenOut, EvalSuite/Case/Run, Baseline
├── lib/api/refusals.ts                 # NEW  code → message table (D16)
├── lib/store.ts                        # EDIT live branches for S1–S20; etags map; delete warnLocalOnly sites as wired
├── lib/data.ts                         # EDIT types: Agent.draftDirty/restoreStatus, Version.id, EvalRun.baseline provenance, Suite baseline
├── app/(app)/agents/[id]/page.tsx      # EDIT save/version/rollback/archive/publish/tokens/cURL/evals/baseline wiring
├── app/(app)/playground/page.tsx       # EDIT target selector (draft/active/version)
├── components/playground.tsx           # EDIT streaming turn state machine, citations, real cost/latency, refusals
└── app/(app)/evaluations/page.tsx      # EDIT live history + run (no client baseline in live)
docs/ (DoD)                             # backend-integration-status (count + 2 rows), 05-eval-drift + 08-api-surface deltas,
                                        # 01-data-model as-built note, implementation-plan, scope.md row, CLAUDE.md
```

**Structure Decision**: existing monorepo layout. New backend logic sits in small `api/` modules (`drafts`,
`baseline`, `eval_store`, `audit`), so the pure parts (`baseline`, `drafts.normalize`) are infra-free testable. The
runtime library gains only the pure fake transport (no FastAPI/redis imports). FE follows the established live-wiring
convention. No new packages, services or dependencies.

## Delivery order (input for /speckit-tasks)

| Phase | Content | Why this order |
|-------|---------|----------------|
| 1 Foundational | migration `0016` + ORM mirror; `audit.py`; fake transport + settings guard + compose env; FE `client.ts` refactor (`parseError`, ETag capture) | everything depends on the schema; the fake transport unblocks every live check without paid calls |
| 2 US1 backend (P1) | draft PATCH + `draft_dirty`; create_version from draft (G3 fix); `created_by`; archive/restore + audit; repoint audit | the editor needs these before any FE work |
| 3 US1 FE (P1) | S1–S7 | first visible win; proves the convention on mutations |
| 4 US2 (P1) | backend `target` + archived refusal; FE `stream.ts`, refusal table, playground S9–S11 | depends on drafts (D3) |
| 5 US3 (P1) | publish advisory (initially `no_eval_run`) + audit; FE S8, S12–S15 | backend tokens already built; advisory becomes meaningful after US4 |
| 6 US4 backend (P2) | suites/cases CRUD; eval-runs body swap; `baseline.py`; `eval_store.py`; history routes; freeze/clear | the largest backend slice; isolated from 2–5 except publish advisory, which reads its table |
| 7 US4 FE (P2) | S16–S20 | |
| 8 Polish | OpenAPI regen + FE build; full regression (≥ 940 / ≥ 413 / 4 / purity); quickstart L1–L24 recorded; DoD doc reconcile + memory | DoD |

US4 backend (phase 6) can run in parallel with phases 3–5 once phase 1 lands, because it touches different files
apart from `routers/evals.py` publish, which sequences after.

## Requirement traceability

| FR | Design | Verified by |
|---|---|---|
| 001 | AgentOut enrichment (api A), S1 | `test_api_agents_draft`, L1 |
| 002 | D1 + D13, S2/S3 | `test_api_agents_draft`, L1–L2 |
| 003 | D2 + D12, S4–S6 | `test_api_versions_from_draft`, L3–L4 |
| 004 | D4, S7 | `test_api_archived_invoke`, L7 |
| 005 | D2 detail + 025 mapping | `test_api_versions_from_draft`, L5 |
| 006 | D15, S9/S11 | L8–L9 |
| 007 | D16 | L10 (7/7) |
| 008 | D15 interrupted state | L11 |
| 009 | D3 (`version_id=null`) | `test_invoke_draft_target`, L12 |
| 010 | D5, S8 | `test_api_publish_advisory`, L13 |
| 011 | tokens as built (G18), S12–S14 | L14–L15 |
| 012 | D17, S15 | L14 |
| 013 | D6, api C, S16–S17 | `test_api_eval_suites`, L17 |
| 014 | D6/D9/D14, S19 | `test_api_eval_runs_persisted`, L18–L19 |
| 015 | D7 (`extra="ignore"`, fields dropped unread) | E7, L22 |
| 016 | D7 no-baseline | E3, L18 |
| 017 | D8 | E4, `test_baseline_resolution`, L21 |
| 018 | eval-runs keep deps order + one runtime path | E5, parity test |
| 019 | seams states (frontend-seams.md) | L1–L23 error strips |
| 020 | `useAuth().can` + server 403 | L6, L16 |
| 021 | D19 | L24 |
| 022 | OpenAPI regen + D20 counter | quickstart §2, DoD |
| 023 | D7 + D10 + D11, api D, S20 | `test_api_baseline_freeze`, E1–E2, L19–L20 |

## Rollout / rollback

- **Rollout**: `make up` runs `alembic upgrade head` → 0016 (additive; no preflight needed). Live wiring is behind
  `NEXT_PUBLIC_API_MODE=live` (unchanged). Mock users are unaffected.
- **Semantics change, disclosed**: the old inline eval-run fields (cases, baseline, policy) are **ignored**, as
  the owner chose in O3. `suite_id` is now required, so an old-shape body without it gets 422 for the missing field.
  The only callers are the FE mock (unchanged; mock doesn't call it) and tests, which are rewritten. `mvp/` does not
  call this API.
- **Rollback**: `alembic downgrade 0015` drops all 0016 objects (round-trip tested). A code revert restores the
  inline body. Persisted eval runs are lost on downgrade, which is acceptable pre-v1 and stated.

## Complexity Tracking

| Deviation / risk | Why needed | Simpler alternative rejected because |
|------------------|------------|--------------------------------------|
| Transient snapshot for draft runs (D3, owner O1) | US2-7 requires running the unsaved draft; P6 requires the same runtime path | "Try draft = create a version": every experiment becomes a version |
| New `audit_logs` table ahead of the 018 audit programme | FR-023 requires an audited freeze; a log line is not a queryable per-workspace record | logger-only: not auditable; full 018 programme: out of scope |
| Fake provider transport in a prod-built image | live checks without paid calls (constitution: paid calls are not ordinary tests) | real key for every check: paid; FE-level mocks: prove nothing about the wire. Mitigated by a prod refusal at startup |
| Eval-run body semantics change (old fields silently ignored, owner O3) | the old shape *is* the vulnerability (client-chosen baseline) | 422 reject: louder, but owner chose ignore; risk = a stale client silently runs the server suite (visible via `suite_id`/`total_cases` in the response) |
| FE test-first exception (§9, approved 2026-11-16) | no FE harness exists; adding one is a separate investment | blocking on a harness: delays the v1 blocker |

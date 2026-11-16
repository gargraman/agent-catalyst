# Quickstart — validating 026 Core Loop Live

This is a validation guide, not implementation. It makes **no paid provider calls**: live checks run on the dev-only
fake transport (research D22). Contracts are in [contracts/api.md](contracts/api.md), seams in
[contracts/frontend-seams.md](contracts/frontend-seams.md), and tables in [data-model.md](data-model.md).

## 0. Prerequisites

```bash
source ~/.nvm/nvm.sh; nvm use 24                    # Next 16 needs Node ≥ 20.9
AC_PROVIDER_TRANSPORT=fake make up                  # Postgres/PgBouncer/Redis/LocalStack + migrate (→ 0016) + api
                                                    # (compose api env gains AC_PROVIDER_TRANSPORT: ${AC_PROVIDER_TRANSPORT:-litellm})
cd services && AC_ENV=dev AC_STORAGE_PROVIDER=local ./.venv/bin/alembic current   # expect 0016 (head)
```

A members row for the mock user in the target workspace, as for the existing live mode (CLAUDE.md).

## 1. Backend — automated (Red→Green evidence per task)

```bash
cd services
./.venv/bin/python -m pytest -q -m "not integration"     # ≥ 940 baseline + new infra-free tests; only held-out gate may fail
./.venv/bin/python -m pytest -q -m integration           # ≥ 413 baseline + new; 0 failed
./.venv/bin/python -m pytest -q tests/test_runtime_purity.py
make -C .. test-integration                              # LocalStack 4/4
```

New test files and what each proves:

| File | Proves |
|---|---|
| `test_migration_0016.py` | upgrade/downgrade round-trip; RLS isolation on every new table; `audit_logs` UPDATE/DELETE as `ac_app` → 42501; composite FKs refuse cross-workspace refs |
| `test_api_agents_draft.py` | PATCH description/draft + If-Match; stale → 409; `draft_dirty`; archive → restore round-trip to the prior status; audit rows for repoint/archive/restore |
| `test_api_versions_from_draft.py` | version = draft (prompt/model/bindings); tool + skill bindings **kept** (G3 regression); `created_by`; dangling KB → 404 with `detail.binding` |
| `test_invoke_draft_target.py` | `target=draft` runs the draft prompt through the same runtime path (parity with a saved version of identical content: same output/cost); run row `version_id IS NULL`; published plane ignores/refuses draft |
| `test_api_archived_invoke.py` | console 409 `agent.archived`; published 404; eval-run 409 |
| `test_baseline_resolution.py` (infra-free) | E1–E4: frozen > automatic > none; self-comparison when candidate is active; common-case diff + `baseline_stale` |
| `test_api_eval_suites.py` | CRUD, `content_hash` changes on edit, duplicate name 409, suite-with-runs delete 409, builder vs viewer perms, cross-workspace 404 |
| `test_api_eval_runs_persisted.py` | E3, E5–E8: persisted run + results; body `baseline/policy/cases` ignored (verdict + row identical with and without); spend cap → 0 rows; deadline → `failed`; history lists newest first; isolation |
| `test_api_baseline_freeze.py` | admin can freeze/clear; publisher 403; builder 403; non-completed / other-suite run → 400; audit `baseline.freeze` with `previous_run_id` |
| `test_api_publish_advisory.py` | advisory = latest completed run of that version; none → `no_eval_run`; never blocks; audit `agent.publish` |
| `test_fake_transport.py` | fake transport streams deterministically; `AC_ENV=prod` + `fake` refuses to start |

## 2. Contract regen

```bash
services/.venv/bin/python scripts/emit_openapi.py     # writes frontend/openapi.json (CI openapi-emit smoke uses the same script)
cd frontend && npm run gen:api && npm run lint && npm run build
```

Expect lint clean and the build green. `git diff frontend/openapi.json` shows only the contract deltas in api.md.

## 3. Live checks (record each result for SC-002; `NEXT_PUBLIC_API_MODE=live npm run dev` → http://localhost:3000)

| # | Steps | Expected | Spec |
|---|---|---|---|
| L1 | Open a live agent, change the prompt + description, Save, hard-reload | values persist; "draft changed" badge shown | US1-1, SC-003 |
| L2 | Two tabs: save in tab A, then save in tab B | tab B shows the "changed by someone else" dialog; reload re-applies B's unsaved values | US1-2 |
| L3 | Create version "v2" with a label; reload | v2 on top with label, creator, time, id | US1-3 |
| L4 | Re-point to v1; run in the playground (target active) | `run_start.version` = v1; the Runs page shows v1's id | US1-4 |
| L5 | Bind a KB in the draft, delete the KB, create a version | "a bound knowledge base no longer exists" message, not generic | US1-5 |
| L6 | Switch the dev role to viewer (server role still builder): edit controls hidden; then a real viewer member | the server 403 is surfaced if the override tries | US1-6, FR-020 |
| L7 | Archive → playground + cURL refused; restore → both answer | console "archived" message; published 404; then OK | US1-7, FR-004 |
| L8 | Playground send (fake transport) | tokens stream incrementally; turn cost/latency equal the Runs page row | US2-1 |
| L9 | Agent with a bound KB | citations with document names | US2-2 |
| L10 | Refusals: set a cap below MTD (`seed_limits`), burst past the rate limit, drop the provider key, send `[[fail:transient]]` ≥ 5× (opens the breaker) then a normal message, `[[fail:timeout]]`, viewer without access, deleted version | 7 distinct messages (D16) | US2-3..5, SC-005 |
| L11 | `[[fail:midstream]]` | partial answer kept, marked incomplete, Retry works | US2-6 |
| L12 | Target draft vs active | the turn label shows which ran; draft run has `version_id=null` | US2-7, FR-009 |
| L13 | Publish | advisory verdict shown; publish completes | US3-1 |
| L14 | Mint a token, copy cURL, run it in a terminal with `AC_TOKEN=<secret>` | stream answers; the token list shows masked prefix + expiry + last used after the call | US3-2..3 |
| L15 | Start a long cURL stream, revoke the token | stream cut within ~2 s (`AC_DENYSET_STREAM_RECHECK_S`); next call 401 | US3-4, SC-006 |
| L16 | Builder opens the Publish tab | publish + token controls unavailable | US3-5 |
| L17 | Create a suite with 3 cases; reload | suite + cases persist; visible to another member | US4-1 |
| L18 | Run on v1 (no baseline) | "Insufficient evidence — no baseline"; run in history | US4-4 |
| L19 | Make v1 active; run on v2 | compared to v1's run, `baseline: automatic · run …`; deltas + failing cases | US4-2, US4-8 |
| L20 | Admin freezes v1's run; re-point to v2; run v2 again | `baseline: frozen by … · run …`; publisher and builder see freeze disabled | US4-8, FR-023 |
| L21 | Edit one case, run again | "compared 2 of 3 cases" (or baseline out of date below min-cases) | US4-5 |
| L22 | `curl` eval-runs with a flattering `baseline` field | 201; verdict + baseline provenance identical to the same run without it | US4-3, SC-004 |
| L23 | Second workspace member lists suites/runs | none of the first workspace's | US4-7, SC-007 |
| L24 | Unset `NEXT_PUBLIC_API_MODE`, click through every screen above | mock behaviour unchanged | FR-021 |

## 4. Real-provider smoke — once, owner-approved (research D22 / O5)

**Preconditions**:
- The owner captures a provider key in a dedicated dev workspace.
- That workspace's spend cap is set to **$0.05**.
- The api is restarted with `AC_PROVIDER_TRANSPORT=litellm`.

| Step | Expected |
|---|---|
| Worst-case preflight: (max input tokens + `max_tokens`=256) × `claude-haiku-5-5` profile price | ≤ $0.05, otherwise **stop** and do not call |
| One playground stream, synthetic prompt | streams; `usage.cost_usd` recorded; matches the Runs row |
| One cURL call with a minted token | streams; second run recorded |
| Sum of both `cost_usd` | ≤ $0.05 and ≤ the preflight estimate; retain run ids + usage frames as evidence |

No eval run in the smoke, because the judge doubles calls. Then revoke the token, and the owner may delete the key.

## 5. Not verified by this guide (disclosed)

- Real provider behaviour beyond the two-call smoke in §4 (refusal shapes, 429s, long outputs).
- Automated frontend tests (no FE harness; `parseSseChunk` is kept pure for later).
- Judge calibration. Verdicts stay **advisory**, and constitution §4 forbids a gate on an uncalibrated judge.
- Multi-replica behaviour (pilot is a single replica).

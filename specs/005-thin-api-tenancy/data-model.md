# Data Model — Thin API + Tenancy Slice (M4 slice)

No new database tables or migrations in this slice. The entities below are **API-layer value objects**
(Pydantic models in `services/api/schemas/`) that project the existing ORM (`api/models.py`) and the M3
`evals` types onto the wire, mirroring `frontend/src/lib/data.ts`. Persistence reuses `agents` /
`agent_versions` (migrations `0001`/`0004`); RLS is enforced by `tenant_session` (no model change).

---

## Principal  *(auth, in-memory)*

The resolved caller for a request (`deps.session_principal`; dev stub this slice).

| Field | Type | Notes |
|---|---|---|
| `subject` | `str` | user/service identity. |
| `workspace_claims` | `dict[str, str]` | `workspace_id → role` (e.g. `builder`/`publisher`); authorizes the selected workspace (FR-007). |

Missing principal on a protected route → `AuthRequired` (401). Selecting a workspace absent from
`workspace_claims` → `AccessDenied` (403).

---

## AgentCreate → AgentOut / VersionOut  *(projects ORM `Agent` / `AgentVersion`)*

**AgentCreate** (request): `{ name: str, provider: str, model: str, system_prompt: str, io_contract: dict }`
— the minimum to persist a draft `Agent` (+ its first `AgentVersion` fields). `workspace_id` is NOT in the
body — it comes from the request scope (FR-007), never client-forgeable into another tenant.

**AgentOut** (response; mirrors FE `Agent`): `{ id, name, status, active_version_id, workspace_id, created_at,
versions: list[VersionOut] }`. Carries an **ETag** (the row `xmin`, D7) surfaced as the `ETag` response header.

**AgentPatch** (request): partial `{ name?, status?, active_version_id? }`. Requires an `If-Match` header
(the prior ETag); a stale match → `ConflictStale` (409). `status` transitions limited to the ORM enum
(`draft|published|archived|eval_failing`).

**VersionOut** (response; mirrors a published version): `{ id, agent_id, label, snapshot_hash, prompt_hash,
publishable, created_at }`. Produced by `POST versions`, which writes an immutable `AgentVersion`
(snapshot_hash via `compute_snapshot_hash`). `to_snapshot()` already exists for the invoke path.

Validation: `name` 1–80 chars (ORM `String(80)`); `provider`/`model` non-empty; `io_contract` must contain an
`input_schema` object. Field constraints quoted from `api/models.py` where the column caps apply.

---

## InvokeIn + SSE frames  *(invoke path; wraps `runtime.invoke`)*

**InvokeIn** (request): `{ inputs: dict, version_id?: str }` — `inputs` validated by the runtime against the
version's `io_contract` (not re-validated in the API, P6). Absent `version_id` → the agent's `active_version_id`.

**SSE frames** (response, `text/event-stream`; one JSON per `data:` line, `event:` = type). The re-encoding of
`runtime.providers.ir.NormalizedStreamEvent`. Frame types live in this slice:

| `event:` | Payload (JSON) | Runtime source |
|---|---|---|
| `run_start` | `{run_id, version, model}` | pipeline enter |
| `text_delta` | `{text}` | `EventType.TEXT_DELTA` |
| `reasoning_delta` | `{text}` | `EventType.REASONING_DELTA` |
| `tool_call_start`/`_delta`/`_end` | `{id, name, args?/args_delta?}` | `EventType.TOOL_CALL_*` |
| `usage` | `{input_tokens, output_tokens, cost_usd, cost_is_estimate}` | `EventType.USAGE` (cost from our profiles, P7) |
| `error` | `{code, message}` | `EventType.ERROR` (api_key-scrubbed, no stack) |
| `done` | `{run_id, status}` | pipeline exit / terminal outcome |

(`retrieval`/`citation`/`guardrail`/`approval`/`subagent` frames are later features — not emitted here.)

---

## EvalRunIn → EvalRunOut  *(eval path; wraps `evals.run_eval`)*

**EvalRunIn** (request, inline suite — D5): `{ version_id, cases: list[CaseIn], baseline: BaselineIn,
policy?: PolicyIn }`. `CaseIn`/`BaselineIn`/`PolicyIn` mirror the M3 `EvalCase`/`BaselineAggregate`/
`EvalPolicy` shapes (data-model 004). `policy` defaults to `{min_cases:10, quality_threshold:90,
efficiency_budget_pct:10}`.

**EvalRunOut** (response; maps `evals.EvalRunResult`, mirrors FE `EvalRun`): `{ verdict, reason, quality,
quality_delta, cost_delta, latency_delta, cases, dimensions[], failing_cases[], pairwise_rate }`. Fully
mocked (no live call). Not persisted (eval tables deferred).

---

## PublishOut  *(advisory publish — D6)*

`{ agent_id, version_id, status: "published", advisory_verdict: Verdict | null, advisory_note: str }`.

- **Never** raises `publish.eval_gate`/`publish.low_confidence` (owner decision #3). `advisory_verdict` is the
  verdict if an eval was supplied, else `null`; `advisory_note` states the gate is advisory until κ calibration.

---

## Error envelope  *(reused `api/errors.py` — no new codes)*

`{"error": {code, message, detail?}}`. Codes exercised in this slice: `input.invalid` (400), `auth.required`
(401), `access.denied` (403), `not_found` (404), `conflict.stale` (409), `idempotency.in_progress` (409),
`rate.limited` (429), `provider.unavailable` (502, surfaced as an SSE `error` frame mid-stream), `internal`
(500, generic — never a stack/secret). `publish.eval_gate` exists but is **not raised** here (advisory).

---

## Headers (request/response contract)

| Header | Direction | Purpose |
|---|---|---|
| `X-Workspace-Id` (or `?workspace=`) | request | tenant selection; authorized vs claim (FR-007) |
| `Idempotency-Key` | request (creates) | in-flight dedup → `409 idempotency.in_progress` (D8) |
| `If-Match` | request (PATCH) | optimistic concurrency vs the `xmin` ETag (D7) |
| `ETag` | response (agent get/create) | the row `xmin`, fed back into `If-Match` |

---

## Relationships

```text
Principal ──authorizes──▶ workspace selection ──▶ tenant_session(ws) [ac_app + GUC + RLS]
                                                        │
AgentCreate ─▶ Agent(+AgentVersion) ──get/list/PATCH──▶ AgentOut(ETag=xmin)/VersionOut
AgentVersion.to_snapshot() ─▶ runtime.invoke ──SSE frames──▶ client            (US1, P6)
EvalRunIn(cases+baseline) ─▶ evals.run_eval ─▶ EvalRunOut                       (US4, inline)
publish ─▶ status=published + PublishOut(advisory_verdict)                      (US4, never gates)
```

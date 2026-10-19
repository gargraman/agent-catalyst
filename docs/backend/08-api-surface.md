# 08 — API Surface (REST + SSE, Pydantic, OpenAPI → export)

> Implements `spec.md §B11` (REST + OpenAPI 3.1, not GraphQL) and `§B2.5` (IO contract). The contract
> is the one the built frontend already expects — see `docs/backend-integration-map.md` (code→endpoint)
> and `docs/backend-integration-plan.md` Part 2 (`data.ts`→API mapping). This doc is the
> implementation-level version of both: concrete routes, Pydantic models mapped field-by-field to the
> frontend TS types, the SSE wire format, and the OpenAPI→export path. Names/tables from `01`.

## 1. Conventions

**Two auth planes, two URL prefixes** (reconciling the FE map's `/v1/...` with spec B11):

| Plane | Prefix | Auth | Who calls it |
|---|---|---|---|
| Builder API | `/api/v1/*` | **console session** (OIDC cookie, CSRF) + workspace scope | the Next.js console |
| Published runtime | `/v1/agents/:id/invoke`, `/v1/...` | **caller bearer token** (hashed, scoped) | external apps / cURL |
| Org governance | `/api/v1/org/*` | **console session + `platform_admins` roster** (09 §1, deployment-wide, read-only) | the console's org/admin view (15) |

Both are served by the `api` process (00 §2); different routers, different FastAPI dependencies
(`deps.session_principal` vs `deps.caller_token`). The FE map writes paths as `/v1/...`; in the builder
console those resolve under `/api/v1/...` via the reverse proxy (`backend-integration-plan` Part 2).

The **console session** dependency also reads the OIDC **`groups` claim** and resolves it to
`Principal.teams` (Okta groups → teams, `09 §2.1`); the **caller token** carries `scopes.teams`. A **third
ingress** — messaging channels (`POST /channels/{platform}/events`) — is documented in `12` and is not part
of this builder/runtime catalog; it mints its own identity (teams = the end user's Okta groups).

**Error envelope.** Every non-2xx returns one shape; `api/errors.py` maps typed exceptions → HTTP:

```json
{ "error": { "code": "publish.eval_gate", "message": "Publish blocked: no PASS eval newer than the last config change.", "detail": {"last_trigger_at": "...", "last_pass_at": null} } }
```

| Exception | HTTP | `code` |
|---|---|---|
| `ValidationError` (input contract) | 400 | `input.invalid` |
| unauthenticated | 401 | `auth.required` |
| not a member / RBAC / access-policy deny | 403 | `access.denied` |
| missing row (post-tenant-scope) | 404 | `not_found` |
| publish without passing eval | **409** | `publish.eval_gate` |
| low-confidence suite blocks publish | 409 | `publish.low_confidence` (unless the workspace `eval.govern` override is on — 05 §5) |
| required assertion `detail` missing | 422 | `eval.assertion_detail_required` (05 §2a) |
| build/publish an unregistered agent | **409** | `registration.gate` (14 §2) |
| high-risk tool call denied/expired at the approval gate | **409** | `tool.approval.denied` (03 §2) |
| over monthly budget (hard cap) | **402** | `budget.exceeded` |
| rate limit (per-token, **P1**) / concurrency + spend quota (P2) | **429** | `rate.limited` |
| provider/all-fallbacks failed | 502 | `provider.unavailable` |

> A **dry-run** (`…/test-invoke`, §2) on a tool the caller hasn't bound or isn't permitted to use reuses
> **`403 access.denied`** — no new code. A **sub-agent depth/fan-out breach is a run `stop_reason`**
> (`subagent_depth_exceeded` / `subagent_fanout_exceeded`, 03 §8) surfaced on the run, **not** an HTTP error.

**Pagination:** cursor-based — `?limit=50&cursor=<opaque>`; responses carry `{items, next_cursor}`.
Dashboards use dedicated metrics endpoints (§7), not paginated lists.

**Idempotency:** published invoke accepts `Idempotency-Key` header → dedupes via the partial-unique
index on `runs` (01 §4.7). The first completion's response body is cached in a short-TTL Redis
idempotency store keyed by `(workspace_id, Idempotency-Key)` and replayed verbatim; the `runs` row
stores only metadata (cost/tokens/status), and Langfuse (10) is **never** the authoritative response
source. After the TTL, a replay returns the stored run metadata + a `result_expired` marker rather than
reconstructing output.

**Tenancy:** every builder route takes `workspace_id` from the client's selection (`?workspace=:id` or
`X-Workspace-Id`) but **authorizes it against the session's memberships** before use (09 §3) — selecting
a workspace you don't belong to is a 403, never a leak, so it cannot be forged. The tenancy guard + RLS
(09, 01 §6) then pin and enforce it.

## 2. Endpoint catalog

Faithful to `backend-integration-map.md`. `Auth`: **S**=session, **T**=caller token, **P**=platform-admin
(session + `platform_admins`, 09 §1). `Async`: returns a job/stream (00 §5, 10). `Backs`: the
`frontend/src/lib/data.ts` symbol (via `store.ts`); P0 routes with no FE backer yet are marked *(net FE)*.

### Agents & versions
| Method · Path | Auth | Async | Backs |
|---|---|---|---|
| `GET /api/v1/agents?workspace=:id` | S | — | `useAgents` / `AGENTS` |
| `GET /api/v1/agents/:id` | S | — | `useAgent` (config + versions + bound kb/tools) |
| `POST /api/v1/agents` | S | — | `addAgent` (create draft) |
| `PATCH /api/v1/agents/:id` | S | — | `updateAgent` (config, lifecycle, bind kb/tools, rollback activeVersion) |
| `DELETE /api/v1/agents/:id` | S | — | `deleteAgent` |
| `POST /api/v1/agents/:id/duplicate` | S | — | `duplicateAgent` |
| `POST /api/v1/agents/:id/register` | S | — | *(net FE)* registration gate: set `registered_at` + owner; audited `agent.register` (14 §2) |
| `POST /api/v1/agents/scan` | S | — | *(net FE)* similarity/dedup scan over a draft config → candidate matches (14 §3) |
| `POST /api/v1/agents/import` | S | ✓ | *(net FE)* bulk import a hand-wired agent → draft + first version (14 §4) |
| `GET /api/v1/agents/:id/versions` | S | — | `getVersions` / `VERSIONS` |
| `POST /api/v1/agents/:id/versions` | S | — | "Save as new version" (clone+change; §4.2) |
| `POST /api/v1/versions/:vid/clone` | S | — | clone to new draft |
| `GET /api/v1/models` | S | — | `MODELS` (provider catalog + profiles + pricing; 02) |

### Knowledge / RAG
| Method · Path | Auth | Async | Backs |
|---|---|---|---|
| `GET /api/v1/knowledge-bases?workspace=:id` | S | — | `useKnowledgeBases` |
| `GET /api/v1/knowledge-bases/:id` | S | — | `useKnowledgeBase` |
| `POST /api/v1/knowledge-bases` | S | — | `addKnowledgeBase` → `KnowledgeBaseCreate` (name, embeddingModel, chunkConfig; `embedding_dim` is **server-derived** from the model catalog, never sent by FE) |
| `POST /api/v1/knowledge-bases/:id/sources` | S | **yes** | `addDataSource` (connector sources carry `config{connector,fields,credentialRef}`; web/manual carry `config.fields{depth…}`; + kick ingestion) |
| `POST /api/v1/knowledge-bases/:id/reingest` | S | **yes** | `reingest` (distinct `job_kind='reingest'`, projected into the same `IngestionJobOut`) |
| `GET /api/v1/ingestion-jobs` (poll/SSE) | S | **yes** | `useIngestionJobs` → `IngestionJobOut` (projection of `jobs ⋈ data_sources ⋈ knowledge_bases`; see DTOs §3) |
| `GET /api/v1/connectors` + presigned upload | S | — | `source-picker.tsx`; returns the connector registry (field specs per connector) + file-upload presign (→ MinIO) |
| `POST /api/v1/connectors/credentials` | S | — | capture a connector secret/file → vault; returns `{masked, vaultPath}`. FE masks client-side as a mock stand-in (never persists raw). Shared by tool auth secrets. |
| `POST /api/v1/versions/:vid/retrieval-bindings` | S | — | server-side **version-create artifact** (04 §9): materialized when a version is snapshotted — one row per bound KB, `kb_index_version` pinned, the draft `retrieval_config` broadcast. **No direct FE caller by design** — the FE edits bindings as the flat `knowledge_bases` + single `retrieval_config` on the agent draft (see `AgentPatch`); per-KB config is P2. |

### Evaluations
| Method · Path | Auth | Async | Backs |
|---|---|---|---|
| `GET /api/v1/evals` · `GET /api/v1/agents/:id/evals` | S | — | `useEvalRuns` / `getEvalsForAgent` |
| `POST /api/v1/agents/:id/evals` | S(`eval.run`) | **yes** | `addEvalRun` (queue vs frozen baseline + judge; 05); body names the `suite_id` to score |
| `GET /api/v1/jobs/:id` | S | **yes** | `resolveEvalRun` completion patch |
| `GET /api/v1/agents/:id/eval-suites` | S | — | `useSuitesForAgent` — suites + cases for the agent |
| `POST /api/v1/agents/:id/eval-suites` | S(`eval.author`) | — | `addSuite` — create a suite (01 §4.5) |
| `PATCH \| DELETE /api/v1/eval-suites/:sid` | S(`eval.author`) | — | `renameSuite` / `deleteSuite` |
| `POST /api/v1/eval-suites/:sid/cases` · `PUT \| DELETE …/cases/:cid` | S(`eval.author`) | — | `upsertCase` / `deleteCase`; body = `EvalCaseDef` (input+vars, assertions `[{kind,detail?,hard}]`, rubric `[{name,weight}]`, optional `expected`). **422** on a missing required assertion `detail` (05 §2a) |
| `GET /api/v1/workspaces/:id/eval-policy` · `PUT …` | S · S(`eval.govern`) | — | `useEvalPolicy` / `setEvalPolicy` — per-workspace `EvalPolicy` (minCases, qualityThreshold, efficiencyBudgetPct, allowLowConfidenceOverride); drives the verdict + publish gate (05 §1) |
| `POST /api/v1/versions/:vid/baseline` | S(`eval.govern`) | — | `reFreezeBaseline` — re-freeze the trusted baseline to this version (audited; 05 §4) |

### Publish, tokens, export
| Method · Path | Auth | Async | Backs |
|---|---|---|---|
| `POST /api/v1/versions/:vid/publish` | S | — | publish (409 if no PASS eval; §6, 05) |
| `POST /api/v1/agents/:id/repoint` | S | — | re-point endpoint to a prior passing version |
| `GET /api/v1/agents/:id/tokens` | S | — | `useTokens` (prefixes only) |
| `POST /api/v1/agents/:id/tokens` | S | — | `addToken` (secret shown **once**; 09) |
| `DELETE /api/v1/tokens/:id` | S | — | `revokeToken` |
| `GET /api/v1/endpoints/:eid/export?format=curl\|ts\|python` | S | — | Publish & Export (§5) |
| `GET /api/v1/endpoints/:eid/openapi.json` | S | — | per-endpoint OpenAPI (export source) |

### Runs, metrics, settings
| Method · Path | Auth | Async | Backs |
|---|---|---|---|
| `GET /api/v1/runs?workspace=:id` | S | — | `useRuns` / `RUNS` |
| `GET /api/v1/runs/:id` | S | — | run detail + spans |
| `GET /api/v1/metrics/{spend,volume,latency,activity}` | S | — | dashboard/runs charts (§7) |
| `GET /api/v1/workspaces/:id` | S | — | `useWorkspaceSettings` (name/slug read-only; default provider/model) |
| `PATCH /api/v1/workspaces/:id` | S(admin) | — | `updateWorkspaceSettings` (default provider/model, `access_mode`; slug immutable) |
| `POST /api/v1/workspaces/:id/archive` | S(admin) | — | `archiveWorkspace` (sets `archived_at`, unpublishes endpoints) |
| `GET /api/v1/workspaces/:id/limits` · `PUT …` | S(admin) | — | `useLimits` / `updateLimits` — per-workspace spend + concurrency + timeout. **Monthly spend cap enforced P1; concurrency P2** (07 §3) |
| `GET /api/v1/provider-keys?workspace=:id` | S | — | `useProviderKeys` (masked) |
| `POST /api/v1/provider-keys` | S(admin) | — | `addProviderKey` (vaulted; 09) |
| `POST /api/v1/provider-keys/:id/rotate` · `…/revoke` | S(admin) | — | `rotateProviderKey` / `revokeProviderKey` (audited; 09 §12) |
| `GET /api/v1/members?workspace=:id` | S | — | `useMembers` — space roster (app-managed; identity from org Okta) |
| `POST /api/v1/workspaces/:id/invites` · `GET …` | S(admin) | — | `inviteMember` / `useInvites` — invite a member (`member.manage`); `workspace_invites` (01) |
| `DELETE /api/v1/workspaces/:id/invites/:id` | S(admin) | — | `revokeInvite` (`member.manage`) |
| `DELETE /api/v1/members/:id` | S(admin) | — | `removeMember` — remove from the space roster (`member.manage`) |
| `POST /api/v1/workspaces/:id/join` | S | — | `joinWorkspace` — self-join an `access_mode = 'open'` space; assigns the default join role (09 §3) |
| `PUT /api/v1/{agents\|knowledge\|tools\|mcp\|channels}/:id/access-policy` | S(admin) | — | `setAccessPolicy` (09 §5); body is the unified `AccessPolicy` (`visibility: workspace\|restricted\|private`, role/team lists, `owner?`); each target table carries an `access_policy jsonb` column (01; channels in 12) |
| `GET /api/v1/agents/:id/guardrails` · `PUT …` | S | — | Guardrails tab (per-version config; 06) |
| `GET /api/v1/search?q=` | S | — | topbar + list search |
| `GET /v1/branding` | public | — | `lib/org.ts:ORG` → `OrgBrand` (name/product/idp/domains + token values). **Optional — multi-org deployments only.** The default one-deploy-per-org model bakes branding at build/deploy time (env / `org.config.json`), so there is **no per-request branding call** (13 §2). Unauthenticated, cacheable, not tenant-scoped; sits *above* the workspace planes. |

### Notifications (16)
| Method · Path | Auth | Async | Backs |
|---|---|---|---|
| `GET /api/v1/notifications?workspace=:id` | S | — | *(net FE)* owner inbox + `unread_count`; filtered to the caller's `recipient_subject` (16 §1,§4) |
| `POST /api/v1/notifications/:id/read` | S | — | *(net FE)* mark one read (`read_at`) |
| `POST /api/v1/notifications/read-all` | S | — | *(net FE)* mark all read |

### Org governance (elevated — platform-admin, 15)
| Method · Path | Auth | Async | Backs |
|---|---|---|---|
| `GET /api/v1/org/agents` | P | — | *(net FE)* whole-estate rows {agent, workspace, owner, status, eval verdict, drift, spend_30d, policy tier, compliance_flags}; metadata-only for `private` |
| `GET /api/v1/org/spend` | P | — | *(net FE)* spend rollup by workspace / agent / model |
| `GET /api/v1/org/compliance` | P | — | *(net FE)* non-compliant list (unregistered / un-evaluated / over-budget / no-owner) — consumes `agents.registered_at`/`owner_subject` (14) |

All three route through the elevated `SECURITY DEFINER` path and write `org.report.read` (09 §7,§12).

### Tools & MCP (P2 — routes reserved)
| Method · Path | Auth | Async | Backs |
|---|---|---|---|
| `GET/POST /api/v1/tools` | S(`tool.manage`) | — | `useTools` / `addTool` (HTTP tool carries `httpConfig{method,url,auth,authRef,headers,argsSchema}`; secret → vault, only `authRef` persisted) |
| `GET /api/v1/mcp-servers` · `GET /:id` | S(`mcp.manage`) | — | `useMcpServers` / `useMcpServer` |
| `POST /api/v1/mcp-servers` | S(`mcp.manage`) | yes* | `addMcpServer` (connect + discover) |
| `POST /api/v1/mcp-servers/:id/discover` | S(`mcp.manage`) | yes | re-run `tools/list` |
| `POST /api/v1/versions/:vid/tools` | S(`mcp.manage`) | — | bind tools/MCP tools to version |
| `POST /api/v1/tools/:id/test-invoke` | S(`tool.manage`) | stream | *(net FE)* dry-run a single HTTP/builtin tool in the sandbox (09 §10, 11 §1); body `{args}`; run `source='tool_test'`; audited `tool.test_invoke` (09 §12) |
| `POST /api/v1/mcp-servers/:id/test-invoke` | S(`mcp.manage`) | yes | *(net FE)* spec §D round-trip test — body `{tool, args}`; audited `mcp.test_invoke` (09 §12) |
| `POST /api/v1/tools/:id/risk-tier` | S(admin) | — | set `risk_tier` ∈ read\|write\|destructive (01 `tool_risk_tier`); audited `tool.bind` (09 §12) |

### Approvals (high-risk tool gate — 03 §2, 09 §10)
| Method · Path | Auth | Async | Backs |
|---|---|---|---|
| `GET /api/v1/approvals?workspace=:id` | S | — | *(net FE)* pending-approvals inbox — `tool_approvals` where `status='pending'`, workspace-scoped (01, 09 §8) |
| `POST /api/v1/approvals/:id/decide` | S(`require_role('publisher')`) | — | *(net FE)* body `{decision:'approve'\|'deny', reason?}`; resumes the paused run via `Command(resume=decision)` (03 §2); writes `tool.approval.{approve,deny}` (09 §12) |

### Published runtime (caller token)
| Method · Path | Auth | Async | Backs |
|---|---|---|---|
| `POST /v1/agents/:id/invoke` (JSON or SSE) | **T** | stream | Playground `send()` (preview) + external callers |

> **Playground vs published invoke share one body + one code path.** The playground calls the *same*
> `runtime.invoke` (00 §1) with a console session instead of a token and `run_source="playground"`. The
> FE `runtime-access.ts` decode is a **client-side preview only**; the server re-validates the token and
> computes effective access (09).

## 3. Load-bearing Pydantic models (mapped to `data.ts`)

Generated OpenAPI from these is the single source for the FE typed client (§5). Each model notes the TS
type it mirrors so FE/BE never diverge.

```python
# services/api/schemas/agents.py
from pydantic import BaseModel, Field
from datetime import datetime

class ModelRefOut(BaseModel):            # mirrors TS ModelRef
    provider: str                        # "Anthropic" | "OpenAI" | ...
    model: str
    in_usd: float = Field(alias="inUsd") # per 1M input tokens
    out_usd: float = Field(alias="outUsd")
    context: str
    class Config: populate_by_name = True

class AgentOut(BaseModel):               # mirrors TS Agent
    id: str                              # slug ("triage-copilot")
    name: str
    description: str
    status: str                          # agent_status enum (01 §3)
    provider: str
    model: str
    owner: str
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    active_version: str = Field(alias="activeVersion")
    versions: int
    tags: list[str]
    calls_30d: int = Field(alias="calls30d")
    cost_30d: float = Field(alias="cost30d")
    p95_ms: int = Field(alias="p95ms")
    eval_pass: float = Field(alias="evalPass")
    drift: str                           # none|stable|watch|regressed
    calls_spark: list[int] = Field(alias="callsSpark")
    knowledge_bases: list[str] = Field(alias="knowledgeBases")
    tools: list[str]
    endpoint: str | None = None
    workspace_id: str | None = Field(default=None, alias="workspaceId")
    access_policy: dict | None = Field(default=None, alias="accessPolicy")
    class Config: populate_by_name = True

class AgentCreate(BaseModel):            # addAgent
    name: str
    description: str = ""
    provider: str
    model: str
    system_prompt: str = ""
    tags: list[str] = []

class AgentPatch(BaseModel):             # updateAgent / updateAgentConfig (all optional)
    name: str | None = None
    description: str | None = None
    tags: list[str] | None = None        # editable on the Configure → General section
    status: str | None = None            # publish/unpublish/archive → may trigger eval gate (§6).
                                         #   The console routes these through guarded wrappers
                                         #   (publishAgent/archiveAgent/…), not a free-form status set.
    active_version: str | None = None    # rollback / re-point
    knowledge_bases: list[str] | None = None
    tools: list[str] | None = None
    access_policy: dict | None = None
    # Draft config edits (updateAgentConfig). Frozen into the next version snapshot on publish (01 §4.2);
    # the io_contract carries the typed input variables (name/type/required/default, B2.5), editable via
    # the Configure → Input variables builder.
    model_config_: dict | None = Field(default=None, alias="modelConfig")
    system_prompt: str | None = None
    io_contract: dict | None = None
    memory_config: dict | None = None
    retrieval_config: dict | None = None
    guardrail_config: dict | None = None
    spend_config: dict | None = None
```

```python
# services/api/schemas/versions.py — a version is an immutable snapshot (01 §4.2)
class VersionOut(BaseModel):             # mirrors TS Version
    label: str                           # "v7"
    status: str                          # draft|evaluated|published|deprecated
    model: str
    provider: str
    created_at: datetime = Field(alias="createdAt")
    created_by: str = Field(alias="createdBy")
    eval_verdict: str = Field(alias="evalVerdict")   # pass|fail|low_confidence|none
    note: str
    publishable: bool
    class Config: populate_by_name = True

class VersionCreate(BaseModel):          # "Save as new version" = clone the active + apply changes
    from_label: str                      # base version to clone
    model_config_: dict | None = Field(default=None, alias="modelConfig")  # provider/model/params (02)
    system_prompt: str | None = None
    io_contract: dict | None = None      # typed inputs + output schema (B2.5)
    guardrail_config: dict | None = None # (06)
    knowledge_bases: list[str] | None = Field(default=None, alias="knowledgeBases")  # bound KB ids frozen into the snapshot (04 §9). Omit = inherit from the cloned base.
    retrieval_config: dict | None = Field(default=None, alias="retrievalConfig")      # top_k/threshold/hybrid/reranker/max_ctx_tokens/injection — broadcast into one retrieval_binding per bound KB at snapshot time (01 retrieval_bindings)
    note: str = ""
    # Server freezes snapshot_hash + resolves profile_snapshot at create (01 §4.2), and materializes one
    # retrieval_binding per bound KB with kb_index_version pinned. snapshot_hash MUST incorporate
    # retrieval_config + each binding's kb_index_version — otherwise a retrieval change won't bust the
    # verdict cache (05) and a stale PASS is served. Editing knowledge_bases/retrieval_config is a
    # DRIFT_TRIGGER (05) → publishable=False until a fresh PASS. (VersionOut mirrors TS Version and does
    # not inline the resolved bindings; read them via the version/retrieval-bindings detail.)
```

```python
# services/api/schemas/knowledge.py — mirrors TS KnowledgeBase / DataSource / IngestionJob (data.ts)
class ChunkConfig(BaseModel):            # FE KnowledgeBase.chunkConfig; stored in knowledge_bases.chunk_config
    size: int = 800                      # tokens per chunk
    overlap: int = 120                   # tokens shared between chunks
    index_type: str = Field("HNSW", alias="indexType")   # HNSW | IVF
    class Config: populate_by_name = True

class KnowledgeBaseCreate(BaseModel):    # addKnowledgeBase
    name: str
    description: str = ""
    embedding_model: str = Field(alias="embeddingModel")
    chunk_config: ChunkConfig = Field(default_factory=ChunkConfig, alias="chunkConfig")
    # embedding_dim is SERVER-DERIVED from embedding_model via the model catalog (02); never sent by the FE.
    class Config: populate_by_name = True

class KnowledgeBaseOut(BaseModel):       # mirrors TS KnowledgeBase; stats are server-computed rollups
    id: str
    name: str
    description: str | None = None
    embedding_model: str = Field(alias="embeddingModel")
    chunk_config: ChunkConfig | None = Field(default=None, alias="chunkConfig")
    docs: int                            # count(documents)
    chunks: int                          # count(chunks)
    size_mb: float = Field(alias="sizeMB")
    last_sync: datetime = Field(alias="lastSync")
    status: str                          # ready | indexing | error
    bound_agents: int = Field(alias="boundAgents")   # count(agents where kb_id in knowledge_bases)
    access_policy: dict | None = Field(default=None, alias="accessPolicy")
    # sources[] (DataSourceOut) included on the detail GET
    class Config: populate_by_name = True

class IngestionJobOut(BaseModel):        # mirrors TS IngestionJob — projection of jobs ⋈ data_sources ⋈ knowledge_bases
    id: str
    source: str                          # data_sources.name (via jobs.ref_id → data_source)
    kb: str                              # knowledge_bases.name
    status: str                          # job_status: queued|running|completed|failed|cancelled
    docs: int                            # progress.done (documents written)
    chunks: int                          # count(chunks) written by this job
    started_at: datetime = Field(alias="startedAt")   # jobs.created_at
    duration: str                        # SERVER-FORMATTED "4m 12s" from created_at→updated_at
    # kind='ingestion' and kind='reingest' both project into this DTO.
    class Config: populate_by_name = True
```

```python
# services/api/schemas/invoke.py — the IO contract at the wire (spec B2.5)
class Message(BaseModel):
    role: str                            # system|user|assistant|tool
    content: str

class InvokeInputs(BaseModel):
    messages: list[Message]
    inputs: dict = {}                    # declared typed input vars; validated vs io_contract (400 on miss)
    stream: bool = True
    session_id: str | None = None        # LangGraph checkpointer key (03, memory)

class InvokeResult(BaseModel):           # non-stream mode
    run_id: str
    output: str
    structured: dict | None = None       # when io_contract declares a JSON schema
    citations: list[dict] = []           # {source, docId, chunkId, score} when RAG on (04)
    usage: dict                          # {input_tokens, output_tokens, cost_usd}
    guardrail: str = "passed"
```

```python
# services/api/schemas/evals.py — mirrors TS EvalRun (incl. drill-down)
class EvalDimension(BaseModel):
    name: str; score: float; baseline: float

class EvalCaseFail(BaseModel):
    input: str; issue: str

class EvalRunOut(BaseModel):
    id: str; agent_id: str = Field(alias="agentId"); agent: str
    version: str; baseline: str
    quality: float
    quality_delta: float = Field(alias="qualityDelta")
    cost_delta: float = Field(alias="costDelta")
    latency_delta: float = Field(alias="latencyDelta")
    cases: int
    verdict: str                         # pass|fail|low_confidence
    date: datetime
    trigger: str
    reason: str | None = None
    dimensions: list[EvalDimension] | None = None
    failing_cases: list[EvalCaseFail] | None = Field(default=None, alias="failingCases")
    class Config: populate_by_name = True
```

```python
# services/api/schemas/runs.py & tokens.py
class RunOut(BaseModel):                  # mirrors TS Run
    id: str; agent_id: str = Field(alias="agentId"); agent: str
    version: str
    source: str                          # run_source (01): Playground|API|Eval|tool_test|subagent
    status: str                          # success|error|blocked
    latency_ms: int = Field(alias="latencyMs")
    tokens: int
    cost: float
    time: datetime
    guardrail: str
    caller: str | None = None
    subject_type: str | None = Field(default=None, alias="subjectType")
    class Config: populate_by_name = True

class ApiTokenOut(BaseModel):            # mirrors TS ApiToken — NEVER includes the secret
    id: str; name: str; agent: str; prefix: str   # `agent` = the bound published endpoint (selected at create)
    created: datetime; last_used: datetime | None = Field(default=None, alias="lastUsed")
    expires: datetime | None = None      # from api_tokens.expires_at (FE Create: none|30d|90d)
    scopes: str                          # human-readable display summary (matches FE ApiToken.scopes: string)
    # NOTE: the STRUCTURED authorization claims (api_tokens.scopes jsonb, 01 §4.7 — teams/scopes/subject)
    # are read server-side at invoke (09 §6) and are deliberately NOT returned on the wire.

class ApiTokenCreated(ApiTokenOut):      # returned ONCE on mint
    secret: str                          # shown once; only the hash is stored (01 §4.7, 09)

# services/api/schemas/settings.py — workspace config + per-workspace limits (admin-only writes)
class WorkspaceSettingsOut(BaseModel):   # mirrors TS WorkspaceSettings; name/slug are read-only identity
    default_provider: str | None = Field(default=None, alias="defaultProvider")
    default_model: str | None = Field(default=None, alias="defaultModel")
    archived_at: datetime | None = Field(default=None, alias="archivedAt")
    class Config: populate_by_name = True
class WorkspaceSettingsPatch(BaseModel): # PATCH /workspaces/:id — slug is NOT patchable
    default_provider: str | None = Field(default=None, alias="defaultProvider")
    default_model: str | None = Field(default=None, alias="defaultModel")
    access_mode: str | None = Field(default=None, alias="accessMode")  # 'open' | 'restricted' (01 workspace_access)
    class Config: populate_by_name = True
class LimitsOut(BaseModel):              # mirrors TS Limits; composed from spend_limits+concurrency_limits+workspaces
    monthly_spend_usd: float = Field(alias="monthlySpendUsd")
    max_concurrent: int = Field(alias="maxConcurrent")       # workspace-wide (not per-endpoint)
    request_timeout_s: int = Field(alias="requestTimeoutS")
    class Config: populate_by_name = True
class LimitsPut(LimitsOut):             # PUT /workspaces/:id/limits (admin). Stored P1; ENFORCED P2 (07)
    pass
```

```python
# services/api/schemas/tools.py — tools, risk tiers, approvals, sub-agents, dry-run (P2; names from 01)
from typing import Literal

RiskTier = Literal["read", "write", "destructive"]        # 01 tool_risk_tier

class EgressPolicy(BaseModel):           # mirrors config.egress (01 §4.6); deny-by-default egress schema
    mode: str = "deny_by_default"
    allow: dict = Field(default_factory=lambda: {"domains": [], "cidrs": [], "ports": []})

class ToolOut(BaseModel):                # mirrors TS Tool; HTTP/builtin tool
    id: str; name: str; kind: str                         # http | builtin | mcp
    risk_tier: RiskTier = Field("read", alias="riskTier")            # 01 tool_bindings.risk_tier
    requires_approval: bool = Field(False, alias="requiresApproval") # 01 tool_bindings.requires_approval
    class Config: populate_by_name = True

class McpToolOut(BaseModel):             # a tool discovered from an MCP server (tools/list)
    server_id: str = Field(alias="serverId"); name: str
    risk_tier: RiskTier = Field("read", alias="riskTier")
    class Config: populate_by_name = True

class SubagentConfigIn(BaseModel):       # 01 subagent_configs — a tool that delegates to another agent
    target_agent: str = Field(alias="targetAgent")
    target_version: str | None = Field(default=None, alias="targetVersion")   # omit → active version
    max_depth: int = Field(2, alias="maxDepth")
    max_fanout: int = Field(3, alias="maxFanout")
    input_mapping: dict = Field(default_factory=dict, alias="inputMapping")
    class Config: populate_by_name = True

class SubagentConfigOut(SubagentConfigIn):
    id: str

class ApprovalOut(BaseModel):            # mirrors TS Approval; 01 tool_approvals (inbox + run detail)
    id: str; run_id: str = Field(alias="runId")
    version_id: str = Field(alias="versionId")
    tool: str                            # bound tool name (tool_binding_id → name)
    risk_tier: RiskTier = Field(alias="riskTier")
    args_redacted: dict = Field(alias="argsRedacted")     # secrets scrubbed (09 §9)
    status: str                          # approval_status: pending|approved|denied|expired (01)
    requested_at: datetime = Field(alias="requestedAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    decided_by: str | None = Field(default=None, alias="decidedBy")
    decided_at: datetime | None = Field(default=None, alias="decidedAt")
    decision_reason: str | None = Field(default=None, alias="decisionReason")
    class Config: populate_by_name = True

class ApprovalDecision(BaseModel):       # POST /approvals/:id/decide
    decision: Literal["approve", "deny"]
    reason: str | None = None

class ToolTestInvokeIn(BaseModel):       # POST /tools/:id/test-invoke — sandbox dry-run
    args: dict = {}
class ToolTestInvokeOut(BaseModel):      # streamed/terminal dry-run result
    run_id: str = Field(alias="runId")   # a run with source='tool_test' (01 run_source)
    result: dict | None = None
    error: dict | None = None            # {code, message} on sandbox/egress/validation failure
    class Config: populate_by_name = True

class McpTestInvokeIn(BaseModel):        # POST /mcp-servers/:id/test-invoke (spec §D round-trip)
    tool: str
    args: dict = {}
class McpTestInvokeOut(ToolTestInvokeOut):
    pass
```

> **FE/BE field alignment is load-bearing.** The FE already uses camelCase (`calls30d`, `qualityDelta`).
> Models use `alias=` + `populate_by_name` so the JSON is camelCase while Python stays snake_case, and
> the generated TS client (§5) matches `data.ts` exactly — the integration is "swap the data source,"
> not a UI rewrite (`backend-integration-plan` Part 2).

## 4. SSE streaming (playground + published invoke)

The runtime yields `NormalizedStreamEvent`s (the IR; 02); the HTTP layer re-encodes them as SSE. One
wire format for both callers, so the FE renders playground and production identically.

**Framing:** `text/event-stream`, one JSON object per `data:` line, `event:` = the type.

```
event: run_start
data: {"run_id":"run-7f3a21","version":"v7","model":"claude-opus-5-5"}

event: guardrail
data: {"stage":"pii","phase":"input","action":"pass"}

event: retrieval
data: {"chunks":4,"kb":"xdr-playbooks"}

event: text_delta
data: {"text":"Based on the "}

event: tool_call_start
data: {"id":"tc_1","name":"http-enrich-ip","args_partial":""}

event: tool_call_end
data: {"id":"tc_1","result_summary":"enriched 1 IP"}

event: citation
data: {"source":"xdr-playbooks","docId":"pb-14","chunkId":"c-3","score":0.82}

event: usage
data: {"input_tokens":9120,"output_tokens":540,"cost_usd":0.034,"cache_hit":false}

event: done
data: {"run_id":"run-7f3a21","status":"success","guardrail":"passed"}
```

| `event:` | Payload | Source |
|---|---|---|
| `run_start` | run_id, version, model | runtime enter |
| `text_delta` | text | model stream (02 IR `text_delta`) |
| `reasoning_delta` | text | model stream (reasoning models; profile-gated, 02) |
| `tool_call_start/_delta/_end` | id, name, args, result | tool loop (03) |
| `retrieval` | chunks, kb | retriever (04) |
| `citation` | source, docId, chunkId, score | retriever (04) |
| `guardrail` | stage, phase, action (pass\|redact\|block) | guardrail runner (06) |
| `approval_required` | approval_id, tool, args_partial, risk_tier | tool approval gate (03 §2) |
| `subagent_start` / `subagent_end` | child_run_id, depth | sub-agent tool call (03 §8) |
| `usage` | tokens + `cost_usd` + `cache_hit` | gateway cost calc (02, 07) |
| `error` | code, message | typed error (fail-open/closed per 06) |
| `done` | run_id, status, guardrail | runtime exit |

Implementation notes: use `StreamingResponse` with `media-type text/event-stream`; disable proxy
buffering + set long read timeouts (`11 §networking`); heartbeat `: ping` every ~15s to keep
intermediaries from closing idle streams; on client disconnect, cancel the runtime task (frees the
provider call + any spend reservation, 07). A **blocked** input guardrail emits `guardrail{action:block}`
then `done{status:blocked}` with no model call — matching FE `Run.status="blocked"` /
`guardrail:"PII blocked on input"`.

A high-risk tool call that hits the **approval gate** (03 §2) emits `approval_required{approval_id, tool,
args_partial, risk_tier}` and the SSE stream parks until `POST /approvals/:id/decide` lands, then resumes
in place. A **non-interactive** (non-SSE) caller instead gets a terminal response carrying
`stop_reason="awaiting_approval"` + `{approval_id}`; it decides out-of-band and re-reads the run. A
denied/expired gate ends the run with `done{status:blocked}` + `stop_reason="approval_denied"` and the tool
is never executed. A sub-agent tool call brackets its child run with `subagent_start`/`subagent_end`.

## 5. OpenAPI as source of truth → export

FastAPI emits OpenAPI 3.1 from the Pydantic models above. Two consumers:

1. **FE typed client (CI).** `openapi-typescript` + a thin fetch wrapper generate `frontend/src/lib/api/`
   in CI from the live spec; a CI check fails if the committed client drifts from the spec
   (`backend-integration-plan` Part 2, risk #5). This is what keeps `data.ts` types and backend models
   in lockstep.
2. **Per-endpoint export** (`GET /endpoints/:eid/export?format=curl|ts|python`). The published endpoint
   has its own narrowed OpenAPI (only `POST /v1/agents/:id/invoke` with *that agent's* resolved input
   schema from its `io_contract`). Snippets are rendered from that spec.

```bash
# format=curl — only the scoped caller token placeholder; NEVER a provider key (09)
curl -N https://agent-platform.internal/v1/agents/soc-triage-copilot/invoke \
  -H "Authorization: Bearer $AGENT_PLATFORM_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Triage alert 8831"}],"inputs":{"severity":"high"},"stream":true}'
```

> **Secret-free export is automated-tested** (`backend-integration-plan` M4): the export output is
> scanned for key-shaped strings; any provider/tool secret in a snippet fails CI. The export supports
> the headline metric "new agent → published, callable endpoint < 15 min" (`scope.md`).

## 6. Publish flow (the eval gate, enforced server-side)

`POST /api/v1/versions/:vid/publish`:
1. Load version; assert `publishable = true` — set only by a PASS `eval_run` **newer than** the last
   config-change trigger on this version (05). Else **409 `publish.eval_gate`**.
2. If the suite is below the low-confidence threshold (default 10 cases) → **409 `publish.low_confidence`**
   unless an audited override flag is passed (05, `spec B5`).
3. Create/repoint `published_endpoints.pinned_version_id`; set agent `status=published`; write
   `audit_logs(action="publish")` (01 §4.8).

`POST /api/v1/agents/:id/repoint {version_label}` re-points to a prior **already-passing** version
(one-click rollback; `backend-integration-plan` §cross-cutting). Editing the agent afterward changes
nothing live until another eval-gated publish — enforced because the endpoint pins a *version snapshot*,
not the live agent (00 §4, 01 §4.2).

## 7. Metrics endpoints (dashboard charts)

The FE dashboard/runs pages render `SPEND_SERIES`, `RUNS_SERIES`, `LATENCY_SERIES`, `ACTIVITY`, and
stat deltas (`dashStats`, `runsStats`, `evalStats`). Back them with time-series endpoints computed from
`runs`/`spans`/`eval_runs` (01), scoped to the active workspace:

```
GET /api/v1/metrics/spend?workspace=:id&range=30d&bucket=4d   -> { labels:[...], series:[{name, values:[...]}] }
GET /api/v1/metrics/volume?workspace=:id&range=7d&bucket=1d   -> RUNS_SERIES shape
GET /api/v1/metrics/latency?workspace=:id&range=6d&bucket=1d  -> LATENCY_SERIES shape (p50/p95)
GET /api/v1/metrics/activity?workspace=:id&limit=20           -> [{who,action,target,time,tone}]  (audit-derived)
GET /api/v1/agents/:id/dashboard                              -> per-agent rollup (calls/cost/p95/evalPass/drift)
```

```python
class Series(BaseModel):                 # mirrors TS Series
    name: str
    values: list[float]

class MetricsOut(BaseModel):
    labels: list[str]
    series: list[Series]
```

Implementation: pre-aggregate in SQL with `date_trunc` + `width_bucket`; cache per
`(workspace, metric, range)` in Redis with a short TTL (dashboards tolerate seconds of staleness) — a
cheap COGS/latency win (07). High-volume raw rows are partitioned/retained per `11 §telemetry` so these
rollups stay fast and the primary DB doesn't bloat. Stat deltas (`spendDelta`, `evalDelta`, …) are
computed as current-window vs previous-window from the same aggregates, not hardcoded as in the mock.

→ Next: `09-security-tenancy.md`.

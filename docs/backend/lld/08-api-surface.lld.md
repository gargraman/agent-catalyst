# 08 (LLD) — API Surface (query contracts, auth matrix, validation, concurrency)

> **Parent:** [`../08-api-surface.md`](../08-api-surface.md) (the ~80-route catalog, the DTOs, the SSE wire
> format, publish flow, metrics).
> The parent is the most implementation-ready design doc — it has the routes, the Pydantic DTOs, and the SSE
> frames. What it leaves open is the stuff a builder hits *per route*: which filters/sort each list accepts,
> who (which claim) may call each operation, the request bodies for the ~15 action/FE routes, field
> validation rules, the uniform async-job response, and optimistic concurrency. This doc fills those.

## What this adds over parent `08`

| Gap in `08` | Filled here |
|---|---|
| Pagination declared once (`§1`), but **no list endpoint says its filters/sort** — the biggest per-route gap | §1 — the query contract per list endpoint |
| Auth is plane-level (`S`/`T`/`P`) + scattered claim annotations; the **claim→operation matrix lives in `09`** | §2 — the matrix surfaced at the API layer, derived from `permissions.ts` |
| ~15 action/FE routes (`register`, `scan`, `import`, `repoint`, `archive`, notifications, `risk-tier`) described in **prose**, not typed | §3 — their request/response bodies |
| `AgentCreate`/`AgentPatch` etc. have **no field constraints**; `status` is a bare `str` | §4 — validators + the status-transition guard |
| async routes marked `✓` but **no uniform submission response** | §5 — `202 {job_id}` + `JobOut` + poll/SSE |
| **concurrent PATCH** on an agent/version unaddressed (only idempotency + publish gate) | §6 — `ETag`/`If-Match` optimistic concurrency |
| no single map of **FE operations ↔ endpoints** incl. the mock-only ones | §7 — the flow parity table |

Python 3.12, Pydantic **v2** (`ConfigDict`/`field_validator`). Snippets compile (§8). The route catalog,
DTOs (`AgentOut`/`AgentPatch`/…), SSE frames, publish flow, metrics: **see `08` — not restated.** Errors +
pagination + idempotency: [`00-conventions.lld.md`](00-conventions.lld.md). State machines:
[`01-data-model.lld.md §5`](01-data-model.lld.md). `inputs` vs `io_contract` validation:
[`03-runtime.lld.md §3`](03-runtime.lld.md).

> Note: parent `08 §3` DTOs use Pydantic **v1** style (`class Config: populate_by_name = True`). The LLD
> convention (README) is **v2** (`model_config = ConfigDict(...)`); the models below use v2. Harmonizing the
> parent's DTOs to v2 is a flagged follow-up, not done here.

---

## 1. Per-list query contract (filters, sort, pagination)

`00-conventions.lld.md §2` gives `Page[T]`, the cursor codec, `clamp_limit`, and `validate_sort`. This binds
them to each list route: the **whitelist** of filters + sort keys. Anything off-whitelist → `400 input.invalid`.

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class ListQuery(BaseModel):
    """Base for every list endpoint's query params. workspace is required + authorized (08 §1 tenancy)."""
    model_config = ConfigDict(extra="forbid")
    workspace: str
    limit: int = Field(50, ge=1, le=200)      # clamp_limit bounds (00-conv §2)
    cursor: str | None = None
    sort: str | None = None                   # validated against the per-route allowlist below
    q: str | None = None                      # free-text search where the route supports it
```

| List route | Filters (query params) | Sort keys (`-` = desc) | Default sort |
|---|---|---|---|
| `GET /agents` | `status` (agent_status), `provider`, `tag`, `drift`, `q` (name/desc) | `updated_at`, `name`, `cost_30d`, `calls_30d`, `eval_pass` | `-updated_at` |
| `GET /runs` | `status` (success\|error\|blocked), `agent_id`, `source` (run_source), `from`/`to` (created_at range), `caller` | `created_at`, `latency_ms`, `cost` | `-created_at` |
| `GET /evals` · `/agents/:id/evals` | `verdict`, `agent_id`, `from`/`to` | `date`, `quality` | `-date` |
| `GET /knowledge-bases` | `status` (ready\|indexing\|error), `q` | `last_sync`, `name` | `-last_sync` |
| `GET /skills` | `status` (draft\|published\|deprecated), `q` | `last_updated`, `name` | `-last_updated` |
| `GET /tools` · `/mcp-servers` | `kind` / `transport`, `risk_tier`, `q` | `name` | `name` |
| `GET /ingestion-jobs` | `status` (job_status), `kb` | `started_at` | `-started_at` |
| `GET /approvals` | `status` (default `pending`), `risk_tier`, `agent_id` | `requested_at` | `requested_at` (oldest-first — the queue) |
| `GET /notifications` | `kind` (notification_kind), `unread` (bool) | `created_at` | `-created_at` |

```python
# Example: the agents list query (one per list route; the rest follow the table).
AGENT_SORTS = {"updated_at", "name", "cost_30d", "calls_30d", "eval_pass"}

class AgentListQuery(ListQuery):
    status: str | None = None
    provider: str | None = None
    tag: str | None = None
    drift: str | None = None
    # router: keyset page over the chosen sort (00-conv §2); server calls validate_sort(sort, AGENT_SORTS, "-updated_at")
```

- **Server-side access-policy filtering happens AFTER these filters** (`09 §5`): a list returns only rows the
  caller may *see*, then applies the query filters — so a `restricted` agent the caller can't see never
  appears even if it matches `status=published`. (The store hooks assume this, `§7`.)
- **Metrics are NOT lists** (`08 §7`) — they take `range`/`bucket`, not `limit`/`cursor`.

---

## 2. Claim → operation auth matrix

`08 §2` annotates routes `S`/`T`/`P` + occasional claims (`eval.author`, `tool.manage`). The authoritative
role→permission map is `frontend/src/lib/permissions.ts` (mirrored server-side in `09`). This surfaces it at
the API layer so each route's `require(permission)` dependency is unambiguous. Role ladder:
`viewer < builder < publisher < admin` (`permissions.ts ROLE_ORDER`); the caller plane is `executer`
(execute-only, token/channel).

| Operation (routes) | Permission (`permissions.ts`) | Min role |
|---|---|---|
| read/list anything in-workspace; playground | `view` / `playground.use` | **viewer** |
| create/edit agent; bind KB/tools/skills; author+run evals; manage KB/tools/MCP/skills/channels | `agent.create`/`agent.edit`/`eval.run`/`eval.author`/`knowledge.manage`/`tool.manage`/`mcp.manage`/`skill.manage`/`channel.manage` | **builder** |
| publish / re-point an agent; decide tool approvals | `agent.publish` / `tool.approve` | **publisher** |
| delete agent; manage provider keys; set access policy; eval policy (`eval.govern`); workspace settings/limits; set tool risk-tier; workspace CRUD | `agent.delete`/`key.manage`/`access.manage`/`eval.govern`/`settings.manage` | **admin** |
| org governance reads (`/org/*`) | `org.report.read` (app-wide `ap-platform-admin`) | **admin (platform)** |
| `POST /v1/agents/:id/invoke` (published) | — (token scope) | **executer** |

```python
from fastapi import Depends

def require(permission: str):
    """FastAPI dependency: 403 access.denied if the session principal's role lacks `permission`
    (hasPermission(role, permission), permissions.ts mirrored in 09). Composes AFTER tenancy (08 §1)."""
    async def _dep(principal = Depends(session_principal)):
        if not has_permission(principal.role, permission):
            raise AccessDenied(f"missing permission: {permission}", detail={"permission": permission})
        return principal
    return _dep

# usage, per the table:  @router.delete("/agents/{id}", dependencies=[Depends(require("agent.delete"))])
```

- **`access-policy` deny is separate from RBAC** (`09 §5`): a builder with `agent.edit` still can't edit a
  `restricted`/`private` agent they're not on the policy for → also `403 access.denied`. Both gates apply;
  `require(...)` is the role gate, `canManage(policy, principal)` (frontend `access-policy.ts`, mirrored in
  `09`) is the resource gate.
- **`executer` holds none of these console permissions** — it can only invoke published endpoints; every
  builder route is `S` (session), never `T` (token).

---

## 3. Action / FE-net route bodies (the untyped ~15)

The parent describes these in prose; here are the request/response models (mirroring the `08 §3` DTO style).

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


# --- Adoption / governance (14), net-FE ---
class RegisterIn(BaseModel):              # POST /agents/:id/register
    owner_subject: str = Field(alias="ownerSubject")   # routable OIDC subject (01 §4.2 owner_subject)
    model_config = ConfigDict(populate_by_name=True)

class ScanMatch(BaseModel):              # one dedup candidate (14 §3)
    agent_id: str; name: str; workspace: str
    similarity: float                     # cosine over the agent_signatures embedding (01 §4.9)

class ScanOut(BaseModel):                # POST /agents/scan → candidate reuse matches
    matches: list[ScanMatch]

class ImportIn(BaseModel):               # POST /agents/import (async) — bulk import a hand-wired agent
    name: str; provider: str; model: str
    system_prompt: str = ""
    source: str = "manual"                # where it came from (audit)

# --- Publish / re-point ---
class RepointIn(BaseModel):              # POST /agents/:id/repoint
    version_label: str = Field(alias="versionLabel")   # a prior ALREADY-PASSING version (08 §6)
    model_config = ConfigDict(populate_by_name=True)

# --- Workspace lifecycle ---
class WorkspaceArchiveIn(BaseModel):     # POST /workspaces/:id/archive  — reversible (08 §2)
    active: bool = False                  # False = deactivate (unpublish endpoints); True = reactivate (net-new)

# --- Notifications (16), net-FE ---
class NotificationOut(BaseModel):
    id: str; kind: str                    # notification_kind (01 §3)
    severity: str                         # info|warning|critical
    title: str; payload: dict = {}
    created_at: str = Field(alias="createdAt")
    read_at: str | None = Field(default=None, alias="readAt")
    model_config = ConfigDict(populate_by_name=True)

class NotificationsOut(BaseModel):
    items: list[NotificationOut]
    unread_count: int = Field(alias="unreadCount")
    model_config = ConfigDict(populate_by_name=True)

# --- Tool risk tier (admin) ---
class RiskTierPut(BaseModel):            # POST /tools/:id/risk-tier
    risk_tier: str = Field(alias="riskTier")   # read|write|destructive (01 tool_risk_tier)
    model_config = ConfigDict(populate_by_name=True)
```

`register`/`scan`/`import` return/augment `AgentOut`; `repoint`/`archive`/`risk-tier` return the updated
`AgentOut`/`WorkspaceSettingsOut`/`ToolOut`. `scan` is sync (a vector lookup); `import` is async (`§5`).

---

## 4. Field validation rules

`AgentCreate`/`AgentPatch` (`08 §3`) carry no constraints. The rules (Pydantic v2 validators):

```python
from __future__ import annotations
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
PROVIDERS = {"anthropic", "openai", "google", "mistral", "meta", "azure", "bedrock"}  # provider_name (01 §3)


class AgentCreate(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field("", max_length=2000)
    provider: str
    model: str = Field(min_length=1)
    system_prompt: str = Field("", max_length=50_000)
    tags: list[str] = Field(default_factory=list, max_length=20)   # ≤ 20 tags

    @field_validator("provider")
    @classmethod
    def _known_provider(cls, v: str) -> str:
        if v.lower() not in PROVIDERS:
            raise ValueError(f"unknown provider: {v}")             # → 400 input.invalid
        return v.lower()                                            # store lowercase (01 §3 enum)

    @field_validator("tags")
    @classmethod
    def _tag_shape(cls, v: list[str]) -> list[str]:
        for t in v:
            if not (1 <= len(t) <= 40):
                raise ValueError(f"tag out of length 1..40: {t!r}")
        return v
    # slug is NOT accepted: the server derives it from name (slugify, iam.ts mirror) and enforces
    # unique(workspace_id, slug) (01 §4.2). A collision → 409 conflict.
```

**Status is not free-form.** `AgentPatch.status` must not let a client jump the lifecycle (`01 §5` state
machine). The console already routes through guarded wrappers (`publishAgent`/`archiveAgent`); the API
enforces it:

```python
class AgentPatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    # ... other optional fields per 08 §3 ...

    @field_validator("name")
    @classmethod
    def _no_blank(cls, v): return v
    # `status` is deliberately ABSENT from PATCH. Lifecycle transitions go through the dedicated endpoints
    # (POST .../publish, .../repoint, .../archive) which enforce the 01 §5 transition table + the eval gate
    # (08 §6). A generic status write would bypass both — so it's rejected (extra="forbid" → 400).
```

> ⚠️ **Amendment to `08 §3`:** `AgentPatch` currently includes `status: str | None` with a prose note that
> the console "routes through guarded wrappers." Prose doesn't enforce it — a direct API caller could
> `PATCH {status:"published"}` and skip the eval gate. Drop `status` from `AgentPatch` (above) and keep
> lifecycle changes on the dedicated endpoints only. Flagged, not edited in the parent.

The jsonb config fields (`model_config`/`io_contract`/`retrieval_config`/`guardrail_config`/…) validate
against the typed models in [`01-data-model.lld.md §3`](01-data-model.lld.md), not bare `dict`.

---

## 5. Async job-submission contract

`08 §2` marks many routes async (`✓`) but never fixes the submission shape. Uniform contract:

- An async `POST` returns **`202 Accepted`** with `{"job_id": "..."}` immediately (never blocks the request).
- The client polls `GET /jobs/:id` → `JobOut`, or subscribes to the job's SSE where one exists (ingestion/eval
  stream progress; `05 §7`, `04`).
- Terminal states are `completed`/`failed`/`cancelled` (`job_status`, `01 §3`); the result lands on the target
  row (`eval_runs`, `knowledge_bases`, …), which the client re-reads.

```python
from __future__ import annotations
from pydantic import BaseModel, ConfigDict, Field


class JobSubmitted(BaseModel):           # 202 body for every async POST
    job_id: str = Field(alias="jobId")
    model_config = ConfigDict(populate_by_name=True)

class JobOut(BaseModel):                  # GET /jobs/:id — mirrors jobs (01 §4.8)
    id: str
    kind: str                             # job_kind: ingestion|eval|mcp_discovery|reingest|model_deprecation_scan
    status: str                           # job_status
    progress: dict = {}                   # {done, total, message}
    error: str | None = None              # set when status == "failed"
    ref_id: str | None = Field(default=None, alias="refId")   # the eval_run / ingestion target (01)
    model_config = ConfigDict(populate_by_name=True)
```

Which `08 §2` routes this covers: `POST /agents/import`, `POST /knowledge-bases/:id/sources`,
`/reingest`, `POST /agents/:id/evals`, `POST /mcp-servers` + `/discover`. The eval case already matches the FE
`resolveEvalRun` completion-patch flow (`05 §7`). `GET /jobs/:id` is the one the FE polls.

---

## 6. Optimistic concurrency (`ETag` / `If-Match`)

`08` covers idempotency (published invoke) and the publish advisory lock (`05 §5`), but not two console users
editing the **same agent** concurrently — a last-writer-wins `PATCH` silently clobbers.

```python
import hashlib

def etag_for(row) -> str:
    """Weak ETag over the row's mutable identity — updated_at is enough (monotonic per write)."""
    return 'W/"' + hashlib.sha256(f"{row.id}:{row.updated_at.isoformat()}".encode()).hexdigest()[:16] + '"'
```

- `GET /agents/:id` (and other editable resources) returns an **`ETag`** header (`etag_for(row)`).
- A mutating `PATCH` **may** send `If-Match: <etag>`. If present and it no longer matches the current row
  (someone else wrote in between) → **`409 conflict`** (`code: conflict.stale`, reusing `ConflictError` from
  `00-conventions.lld.md §1` — no new HTTP status), so the client re-fetches and re-applies.
- `If-Match` is **optional** for backward-compatibility (the FE mock doesn't send it today); when absent the
  behavior is the current last-writer-wins. A hardened console sends it on every edit form.

> ⚠️ **Amendment to `08 §1`:** add the `If-Match`/`ETag` convention + the `409 conflict.stale` code to the
> error table (or `412 precondition.failed` if you prefer the REST-purist status — but that needs a new 412
> row the table lacks; `409 conflict` reuses an existing status). Flagged, not edited in the parent.

---

## 7. Flow-level FE parity (store.ts operations ↔ endpoints)

The "verify flow wrt frontend" deliverable for the API layer: every `store.ts` operation maps to a `08`
endpoint. ✅ = contract-faithful, ⚠️ = mock-only / divergent (the server does something different).

| FE op (`store.ts`) | Endpoint | Note |
|---|---|---|
| `useAgents`/`useAgent` | `GET /agents` / `/agents/:id` | ✅ workspace-scoped + access-policy filtered (§1) |
| `addAgent` / `updateAgent` / `updateAgentConfig` | `POST /agents` / `PATCH /agents/:id` | ✅ `AgentCreate`/`AgentPatch` (§3/§4); config edits set `needsReEval` (01 LLD §7) |
| `publishAgent`/`unpublishAgent`/`archiveAgent`/`restoreAgent`/`rollbackVersion` | `POST /versions/:vid/publish`, `/agents/:id/repoint`, lifecycle | ✅ via dedicated endpoints (not PATCH status, §4); eval gate (08 §6) |
| `deleteAgent` | `DELETE /agents/:id` | ✅ server cascades; FE also does a client-side cascade of denormalized refs |
| `duplicateAgent` | `POST /agents/:id/duplicate` | ✅ materializes resolved config into a new draft |
| `addKnowledgeBase`/`addDataSource`/`reingest` | `POST /knowledge-bases` / `.../sources` / `.../reingest` | ✅ sources/reingest are **async** (§5) → kick a job, fan-out `needsReEval` to bound agents |
| `scheduleIngestionCompletion` | — | ⚠️ **mock-only** async-flip simulator; real completion is the worker + `GET /jobs/:id` |
| `addSkill`/`updateSkill`/`publishSkill`/`deleteSkill` | `POST/PUT/DELETE /skills` + `/publish` | ✅ publish signs the package; content change re-flags bound agents |
| `addTool`/`updateTool`/`deleteTool`; `addMcpServer`/`updateMcpServer`/`reconnectMcpServer`/`setMcpToolEnabled` | `/tools`, `/mcp-servers` (+ `/discover`) | ✅ discovery async; `authMode`/`scope` change = drift trigger |
| `decideApproval` | `POST /approvals/:id/decide` | ✅ `require_role('publisher')`; resumes the parked run (03 §4) |
| `addEvalRun`/`resolveEvalRun` | `POST /agents/:id/evals` (async) / `GET /jobs/:id` | ✅ 202 + job poll (§5) |
| `addSuite`/`upsertCase`/… / `setEvalPolicy` / `reFreezeBaseline` | eval-suite + `/eval-policy` + `/baseline` | ✅ `eval.author`/`eval.govern` (§2) |
| `addRun` | — | ⚠️ **mock-only**; real runs are server-recorded by `runtime.invoke` (no create endpoint) |
| `setAccessPolicy` | `PUT /{…}/:id/access-policy` | ✅ unified `AccessPolicy` (01 LLD §3), admin |
| `addToken`/`revokeToken`; `addProviderKey`/`rotate`/`revoke` | `/tokens`, `/provider-keys` | ✅ secret shown once; keys vaulted, masked on read |
| `updateWorkspace`/`updateWorkspaceSettings`/`updateLimits`/`setWorkspaceActive` | `PATCH /workspaces/:id`, `/limits`, `/archive` | ✅ admin; `archive` reversible |
| `addWorkspace` / `deleteWorkspace` | `POST /workspaces` / `DELETE /workspaces/:id` | ⚠️ **mock-only** (net-new, not in base design — workspaces are org-provisioned; base design soft-archives). Already flagged in `08 §2`. |
| `useMembers` | `GET /members` | ✅ **read-only** claim projection (no roster table) |
| `*Override` (role/team/platform-admin dev toggles) | — | ⚠️ **mock-only** dev previews; removed server-side |

The mock-only rows (`addWorkspace`/`deleteWorkspace`/`addRun`/`scheduleIngestionCompletion`/`*Override`) are
the `@backend remove` markers from the integration map — they vanish when the server owns that data.

---

## 8. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as the other LLDs).
2. **Cross-ref** — against `08` read in full: `§1` (errors/pagination/idempotency/tenancy), the `§2` catalog
   (every route cited exists), `§3` DTOs (referenced, not restated), `§6` publish flow, `§7` metrics;
   `permissions.ts` (the `Permission`/`ROLE_PERMISSIONS` the §2 matrix is built from); `00-conventions.lld.md`
   (`Page`/cursor/`AccessDenied`/`ConflictError`), `01 §3`/`01 LLD §5` (enums + state machine), `03 LLD §3`
   (input validation).
3. **FE flow parity** — the §7 table was built from the `store.ts` operations; the ⚠️ mock-only rows match
   the `@backend remove` markers.
4. **Three flagged amendments** — drop `AgentPatch.status` (gate-bypass), add the `If-Match`/`409 conflict.stale`
   row, harmonize parent DTOs to Pydantic v2 — recorded as callouts, not parent edits.

→ Next LLD: `06-guardrails.lld.md` (subtask 7).

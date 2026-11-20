# Data Isolation & Deployment Topology

> A cross-cutting synthesis of **how data is separated** (tenant / agent / user), **how the vector
> store is isolated**, **what infrastructure is shared**, and **which deployment topology suits** —
> pulling together the decisions that live in `01-data-model.md`, `04-rag.md`, `09-security-tenancy.md`,
> and `11-deployment.md`. It also records the **stateful coding-agent** use case as an explicit
> out-of-scope boundary (§7). Nothing here is new design — it consolidates and argues the existing model.

## 0. TL;DR

Separation is **logical, not physical**. One shared stack; **three nested scoping keys**, each enforced
in the app layer *and* the database:

```
ORG  (exactly 1 per deployment)
└─ WORKSPACE = TENANT        workspace_id on every row  → app scope-guard + Postgres RLS
   └─ RESOURCE (agent/KB/..) access_policy jsonb        → workspace | restricted | private
      └─ USER                owner_subject + per-user DEK (per-user RAG doc-ACLs = P2+)
```

Vectors live in the **same Postgres** (pgvector), one shared `chunks` table namespaced by
`(workspace_id, kb_id)`. **Everything is shared by default**; physical isolation (schema-per-tenant,
dedicated vector store) is a deferred escape hatch, not day-one complexity.

## 1. The three separation levels

### 1.1 Tenant = workspace
Every tenant-owned row carries `workspace_id uuid not null references workspaces(id)` — centralized via
a `WorkspaceScoped` SQLAlchemy mixin (`01-data-model.md:885-887`). The `org` exists but is collapsed to
**one per deployment** — there is no `org_memberships` table (`01-data-model.md:793`,
`09-security-tenancy.md:29-30`).

Isolation is enforced **twice** (`09-security-tenancy.md:161-164`):
1. **App-layer scope guard** — the primary control, gives clean `403`s.
2. **Postgres RLS** — the backstop: if a query ever forgets `WHERE workspace_id = …`, RLS returns zero
   rows instead of leaking. Set per request via `SET LOCAL app.workspace_id = '<uuid>'` after authz
   (`09-security-tenancy.md:156-158`, `01-data-model.md:931-947`). The app role is **not** the table
   owner and has **no `BYPASSRLS`**; a separate migration role owns DDL. Connection checkout is
   **transaction-scoped** (PgBouncer in `transaction` mode) so `SET LOCAL` can't bleed across pooled
   sessions (`09-security-tenancy.md:329`, `01-data-model.md:950-952`).

A CI check asserts every `WorkspaceScoped` table has an RLS policy (`01-data-model.md:984-986`), backed
by a dedicated tenant-isolation test suite as a CI gate (`09-security-tenancy.md:341-373`).

### 1.2 Agent / resource level
Each agent, KB, tool, MCP server, channel, and skill carries `access_policy jsonb`
(`01-data-model.md:174-176`). Three sharing tiers (`09-security-tenancy.md:244-253`):
- **`workspace`** — every space member may use/manage (default).
- **`restricted`** — gated by **role AND team**, evaluated **separately for use vs manage**; a policy
  naming neither role nor team **fails closed** (grants no one).
- **`private`** — only the **owner** (+ app-wide `admin`) may use/manage.

Enforcement is **shared code** across console reads and runtime invoke, re-checked server-side on read,
manage, and — critically — **at invoke** (`09-security-tenancy.md:255-284`).

Caller rule (2026-10-23): for callers (`executer` role), `useRoles` are ignored and gating is on **teams
alone** — a role-only `restricted` policy denies all callers; a `private` resource is denied to every
caller (no owner subject, no admin-token bypass at invoke) (`09-security-tenancy.md:248-293`).

### 1.3 User level
`agents.owner_subject text not null` is a **routable OIDC subject** captured at registration — the owner
key for `private` checks and notifications (`01-data-model.md:164-166`, `09-security-tenancy.md:275`).
Per-user MCP credentials use a **per-user DEK** so user U can't read user V's secrets within the same
tenant (`09-security-tenancy.md:440-441`; test `:351-353`). True per-user *data* scoping ("see only your
own documents" = per-user RAG doc-ACLs) is explicitly **P2+** (`scope.md:182-184`).

### 1.4 The one sanctioned cross-tenant path
Org-governance reporting crosses tenants via a Postgres **`SECURITY DEFINER`** function owned by the
migration role, `EXECUTE`-granted only to a dedicated `platform_report` reader role — the normal app
role still has no `BYPASSRLS`, and every call writes an org-level audit row
(`audit_logs.workspace_id = NULL`) (`09-security-tenancy.md:333-339`). Runs on a **separate DB
connection** (`scope.md:179`).

## 2. Knowledge base / vector DB isolation

pgvector lives **in the same primary Postgres** — not a separate vector DB
(`01-data-model.md:14`, `11-deployment.md:70`). One shared `chunks` table carries `workspace_id`,
`kb_id`, `document_id`, and `embedding vector(N)` (`01-data-model.md:316-338`). A **single shared HNSW
index** serves all tenants/KBs; separation is a WHERE filter aided by the `chunks_ws_kb (workspace_id,
kb_id)` btree that prunes the namespace.

Retrieval is namespace-pruned on every call (`04-rag.md:139-146`):
```sql
select id, text, meta, 1 - (embedding <=> :qvec) as score
from chunks
where workspace_id = :ws and kb_id = :kb
  and 1 - (embedding <=> :qvec) >= :threshold
order by embedding <=> :qvec
limit :k;
```
RLS backstops the row read, and a **live** KB `access_policy` check runs at invoke *before* the query
(`04-rag.md:122-126`). Eval runs honor the frozen snapshot but bypass the per-caller ACL
(`04-rag.md:118-121`). "pgvector queries must *also* filter `kb_id` + `workspace_id` — RLS covers the
row read but you still want the index to prune by namespace" (`01-data-model.md:973-974`).

Key constraints:
- **Embedding model is pinned per KB** (`embedding_model` + `embedding_dim`); changing it = re-ingest +
  drift trigger (`04-rag.md:83-84`, `01-data-model.md:278-290`).
- The fixed-width `vector(N)` column ⇒ **one embedding family per org** so all KBs share N; a genuinely
  different dimension needs a separate `chunks_{dim}` table (`04-rag.md:91-94`). >2000-dim models index
  a `halfvec` cast (pgvector's ANN index caps `vector` at 2000 dims) (`01-data-model.md:340-349`).
- **KB content is live and unversioned** — the agent version pins only the *binding*
  (`retrieval_bindings`, `unique (version_id, kb_id)`), not the chunks. A KB refresh does **not** bump
  the agent version (deliberate asymmetry vs skills, which fold into `snapshot_hash`)
  (`04-rag.md:199-203`, `01-data-model.md:353-362`).

## 3. Infrastructure — what is shared

Every backing service is a **single shared instance**; tenant isolation is logical (RLS + per-workspace
secret namespacing in the secrets backend — AWS Secrets Manager, feature 014, not Vault DEKs; `09 §9`),
never a separate stack per tenant (`11-deployment.md:63-75`).

| Component | Shared? | Per-tenant mechanism |
|---|---|---|
| Postgres + pgvector | shared single DB | `workspace_id` + RLS; vectors namespaced `(ws, kb)` |
| Redis | shared | logical keys: queue, semaphores, cache, budget counters |
| Secrets backend (AWS Secrets Manager / LocalStack — feature 014, **not** Vault; `09 §9`) | shared | per-workspace secret names + RLS on `provider_keys`; raw resolved per-invoke into memory only |
| Langfuse / ClickHouse | shared | rows carry `workspace_id`; ClickHouse derived, **no RLS** |
| MinIO | shared | objects keyed from tenant-scoped rows |
| `api` (FastAPI) | shared, stateless, HPA | tenancy guard in request layer; no tenant state in memory (`11-deployment.md:443`) |
| `worker` (arq/RQ) | shared, stateless | pools split by **job type** (interactive vs batch), not tenant (`11-deployment.md:85-87`) |
| LiteLLM + LangGraph | **in-process libraries** | n/a — imported by `api`/`worker` (`00-overview.md:103-104`) |

Deployable units are only `api`, `worker`, `web`, and (P2) `sandbox` (`00-overview.md:36-45`).

Secrets: provider keys are custodied in a pluggable secrets backend (**AWS Secrets Manager** / LocalStack —
feature 014, reverses the Vault/DEK scheme; `09 §9`); credentials resolve **per-invoke into memory only**,
never baked into images; prod auth via an **IAM role** on the task/pod (no static creds). Tool/source/channel
creds are designed for the same seam but not yet built.

Compute sharing: queues split by job class so a 120-case eval can't starve ingestion
(`11-deployment.md:85-87`); the **per-workspace monthly budget cap (→ HTTP 402)** is the primary tenant
throttle in P1 (`07-cost-cogs.md:43-73`); per-endpoint budgets, concurrency semaphores, and rate caps
are **P2** (`07-cost-cogs.md:48-49`). No app process holds tenant state, so scale-out is safe.

## 4. Topology options (recommended first)

- **A — Pooled: shared DB + shared schema + row-level (CURRENT).** `workspace_id` + RLS. Cheapest, one
  migration set, best utilization. Costs: blast radius (one bad migration/RLS bug = all tenants) and
  noisy-neighbor, mitigated by budget caps + queue split. **Best fit for an internal, one-org,
  tens-of-agents tool — exactly what this is.**
- **B — Bridge: shared DB + schema-per-tenant** (or `chunks_{dim}` tables on the vector side). Named in
  `01-data-model.md:9-11` as the escape hatch "for an unusually large tenant." Better blast-radius +
  per-tenant backup/restore; cost = N schemas to migrate.
- **C — Silo: database- or deployment-per-tenant.** Hardest isolation, data residency, independent
  scaling; highest ops cost. Only justified for **external multi-customer SaaS** or compliance/residency.
- **Vector sub-axis:** shared HNSW + namespace prune (now) → per-dim tables → dedicated vector store
  (Qdrant/Milvus or partitioned pgvector) past **~5–10M chunks/deployment** (`04-rag.md:160-163`).

Decision drivers: # of tenants, size of the *largest* tenant, compliance/residency, blast-radius
tolerance, ops budget. On every driver except external-SaaS/compliance, **A wins** at this scale.
External validation: the tenancy posture (app-guard + RLS backstop + no-`BYPASSRLS` + PgBouncer-txn +
SECURITY-DEFINER org plane) was assessed **stronger** than an app-layer-only competitor (`scope.md:187`).

## 5. Tradeoffs to accept (or revisit)

- **Pooled = one database.** The defense-in-depth is genuinely strong, but a single RLS/migration slip
  is all-tenant blast radius. Fine internally; a liability if ever sold externally.
- **Single `vector(N)` column couples the whole org to one embedding family.** A real constraint —
  mixing dims means extra tables. Make it a conscious call.
- **Concurrency caps are P2.** Today only the *spend* cap (402) protects a tenant; a burst can still
  saturate shared worker/LLM latency. Latency isolation ≠ cost isolation until P2.

## 6. How the mock frontend mirrors this

The mock already enforces the model client-side so the backend swap is drop-in: `tagResource`/`inWs`
scope by `activeWorkspaceId` in `frontend/src/lib/store.ts`; KB hooks (`useKnowledgeBases`,
`useKnowledgeBase`) are `@backend`-marked for the real `workspace_id`-filtered endpoints
(`GET /v1/knowledge-bases?workspace=:id`).

## 7. Out of scope: stateful coding agents (gap analysis)

A use case repeatedly raised — *an agent works on a GitHub repo, fixes a Jira ticket, holds uncommitted
changes across sessions, raises a PR, with multiple developers concurrent on the same repo* — is **out
of scope**, and the reason is architectural, not a backlog gap.

The platform's core invariant is **stateless `invoke()` over immutable snapshots**: no process holds
tenant state (`11-deployment.md:443`), the sandbox is **ephemeral-per-call** and torn down after the
call (`11-deployment.md:155-160`, `00-overview.md:45`), and the only durable state is *conversation
memory* (LangGraph checkpointer keyed `(workspace_id, agent_id, session_id)`) + an *approval-pause*
interrupt — both **P1 and unbuilt** (`session-memory-status.md:14`, `03-runtime.md:112-113,190-200`).
A resumable dirty working tree inverts that invariant.

What exists vs what's missing:

| Requirement | Status |
|---|---|
| Act "as developer X" for the PR | ✅ Designed — per-user MCP identity (OAuth refresh token under per-user DEK; GitHub named). But **PROPOSED P2+**, not in schema/API (`17-mcp-identity.md:3-4,12-15`). |
| Isolated place to run code | ⚠️ gVisor sandbox exists — **P2, ephemeral-per-call, no persistent FS** (`11-deployment.md:69,540`). |
| Durable uncommitted working tree across pause/resume | ❌ Unmodeled. No working-directory/filesystem concept anywhere. |
| Concurrent-dev repo isolation | ❌ Unmodeled. "Concurrency" = spend/throughput throttling. |
| git clone / branch-per-task / PR flow | ❌ Unmodeled. Tool kinds are only `http/builtin/subagent/mcp` (`01-data-model.md:90`); `code-exec` is aspirational. "Git repo" is a read-only RAG connector (`spec.md:278`). |
| Jira integration | ❌ Not a product feature — the dev team's own workflow tooling only. |

**If it were ever supported**, it would add a fourth **work-state data plane** — large, mutable, binary,
must persist across sessions, *cannot be RLS'd like a Postgres row*. Separation keys would extend to
`workspace → user (owner_subject) → work-session (run/task + repo + branch)`. Concurrent-dev isolation
falls out for free if enforced as **one working copy per (user, task) + branch-per-task** (git's native
model; conflicts resolved at PR). Storage topology, recommended first:
- **A. Git-as-persistence (recommended for internal):** no durable FS; pause auto-commits to
  `wip/<user>/<task>` + push; resume re-clones. Closest to the stateless model, zero new stateful infra.
- **B. Persistent per-task volume (bridge):** PVC keyed `(ws, user, task)` mounted into a session-scoped
  sandbox — true fidelity, but owns volume lifecycle/GC/quota and breaks the ephemeral-sandbox design.
- **C. Container/FS snapshot (silo):** CRIU/Firecracker snapshot per pause — highest fidelity and ops.

Either way the sandbox must shift **call-scoped → session-scoped** (long-lived, re-attachable,
idle-hibernate), and budget-cap-only throttling becomes insufficient (need per-user/per-task CPU/mem/disk
quotas). **Recommendation:** keep the platform pure — publish a chat agent that calls a per-user GitHub
MCP, and let a dedicated coding-agent harness own worktree + resume + PR. This boundary is recorded as a
non-goal in `scope.md` (Out of scope for v1) and `spec.md` (A3).

## Status & provenance

- This doc is a **synthesis of existing decisions**, not new design. Every claim cross-references the
  source module and (where load-bearing) a `file:line`.
- Topology **A (pooled, logical isolation)** is the locked P1 choice; B/C are deferred escape hatches.
- The coding-agent use case (§7) is **out of scope** — confirmed 2026-11-03.

→ Next: `09-security-tenancy.md` (the enforcement detail) · `01-data-model.md §4.4/§6` (DDL + RLS) ·
`04-rag.md` (retrieval) · `11-deployment.md` (infra units).

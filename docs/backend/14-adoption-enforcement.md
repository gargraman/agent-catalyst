# 14 — Adoption & Enforcement (import, registration gate, dedup)

> Implements the `scope.md` governing principle — the platform exists to **govern** agents
> (build → configure → test → publish with guardrails + a drift-aware eval gate) — and `spec.md §B3`
> (agents/versions lifecycle). It closes **P0 adoption gap #1**: today the platform governs agents it
> *creates*, but has no designed way to (a) import the ~40 existing hand-wired agents, (b) force new and
> imported agents through the gate, or (c) dedup at registration so the org stops re-building the same
> agent. Governance of agents nobody onboards is worthless; this doc is the on-ramp. Builds on
> `09` (auth/tenancy/secrets), `01` (data model), `08` (API surface), `02`/`04`/`05`/`07` (profiles, KB
> embeddings, eval gate, cost).

## 1. Key-custody enforcement model (the spine)

Governance is only real if an agent **cannot** reach a model except through `runtime.invoke` (00 §1) —
that is where guardrails (06), the access-policy re-check (09 §5), cost accounting (07), and the audit
trail (09 §12) live. Everything downstream (the gate §2, dedup §3, import §4) is theatre if a team can
call Anthropic/OpenAI directly with their own key.

**The platform becomes the sole holder of the org's model-provider credentials.** The per-workspace
"BYO provider key" option (`backend-integration-plan.md` Part 4, lines 263 and 380) is **explicitly
superseded for enforcement**:

> **Why BYO defeats enforcement.** If a workspace holds a *usable* provider key, a team can issue the
> same model call outside the platform — bypassing the eval gate, guardrails, egress controls, and
> audit. Enforcement therefore has a hard precondition: **no tenant ever holds a usable provider key.**
> The platform-shared pool, custodied in Vault (09 §9), becomes the **only** key path. `provider_keys`
> (01 §4.3) keeps its shape, but for enforced orgs every row points at a **platform-scope** `vault_path`
> (not a per-workspace DEK-wrapped key the workspace admin supplied), and the `POST /provider-keys`
> admin route (08 §2) is disabled / read-only at the workspace tier.

Consequences, stated honestly (do not pretend this is free):

- **(a) Per-tenant cost attribution survives.** Cost is computed from our **own capability profiles**
  per Run tagged `workspace_id` (02, 07) — never read from provider billing. Collapsing N per-workspace
  keys into one pool key changes *who holds the secret*, not *how spend is attributed*: every Run still
  carries `workspace_id` + computed `cost_usd` (01 §4.7), so quotas and chargeback are unaffected. This
  is the one property BYO was sold on that we **keep**.
- **(b) Per-workspace secret isolation for the provider key drops to pool-level.** The 09 §8
  isolation-suite assertion *"A's decrypt path cannot resolve B's provider key"* is **rescoped**: the
  pool key is platform-held and never workspace-exposed, so there is no per-workspace provider secret to
  isolate — the test becomes "no workspace-tier caller can resolve the pool key at all" (fail-closed for
  every tenant, not just cross-tenant). The per-workspace DEK (09 §9) still governs **tool creds,
  connector creds, and channel auth** — only the *provider* key moves to a platform-scope secret.

> **The honest prerequisite — custody alone is porous.** Holding the only *custodied* key does not stop
> a determined team from pasting a **personal or other-account** key into a hand-wired script and hitting
> the provider directly. Real teeth require two org-level controls the platform cannot implement by
> itself:
>
> 1. **Cloud-model-IAM centralization** — provider/Bedrock/Vertex access is granted to the platform's
>    service identity only; no team-owned API orgs or cloud model-invoke IAM.
> 2. **Network egress control to provider endpoints** — SCP / VPC egress allow-list so `api.anthropic.com`,
>    `api.openai.com`, Bedrock/Vertex regional endpoints, etc. are reachable **only** from the platform's
>    egress path. This **reuses the per-tool/MCP egress-allow-list machinery already designed** in
>    09 §10 (deny-by-default, domains/CIDRs), applied one layer out at the network boundary.
>
> **Without both**, the enforcement spine degrades in practice to **"visibility + mandate"** — the
> platform is the *sanctioned* path and the audit/cost story holds for compliant teams, but a team that
> wants to go around it still can. This is a political + infra lift (cloud IAM + network ownership),
> owned outside this codebase. Say so to stakeholders; do not let "we custody the keys" be mistaken for
> "we enforce."

## 2. Create-time registration gate

An agent must be **registered** before any version can be built or published. Registration captures the
minimum needed to govern and route accountability:

- `owner_subject` — a **routable OIDC subject** (org Okta `sub`), not a display name. `agents.owner_subject`
  already exists (01 §4.2); registration **tightens its semantics**: it must resolve to a real principal
  (09 §2), so an orphaned agent always has a reachable owner for drift alerts and access decisions.
- `workspace` — the tenant (01 §4.1); pins RLS + cost attribution.
- `access_policy` — the unified `AccessPolicy` (09 §5); defaults to `{"visibility":"workspace"}`.
- intended model — `(provider, model, api_version)`, validated against the capability-profile catalog
  (02 §4) — same check import performs (§4).

> **Register as a flag/record, not a new `agent_status`.** Adding a `registered` value to the
> `agent_status` enum (01 §3) would perturb the eval → lifecycle state machine (`draft → evaluated →
> published`, 05) that every gate and read-model keys off. Instead:

```sql
-- additive column on agents (01 §4.2 is canonical for the DDL; shown here for context)
alter table agents add column registered_at timestamptz;   -- NULL = unregistered; set once by agent.register
-- owner_subject already exists (01 §4.2); registration enforces it is a routable OIDC subject, not a label.
```

```python
# services/api/routers/agents.py   (08 §2 — Agents routes)
async def register_agent(agent_id, body: AgentRegister, ctx = Depends(require_role("builder"))):
    _assert_routable_subject(body.owner_subject)                  # must resolve to an org Okta principal (09 §2)
    _assert_profile_exists(body.provider, body.model, body.api_version)   # capability profile (02 §4) — else 400
    agent = await load_agent(agent_id, ctx)                       # tenant-scoped (09 §3, RLS 01 §6)
    agent.registered_at = now()
    agent.owner_subject = body.owner_subject
    agent.access_policy = body.access_policy or {"visibility": "workspace"}
    await audit(ctx, action="agent.register", target=agent_id, meta={...})  # audit action (09 §12, 01 §4.8)
    await run_similarity_scan(agent, ctx)                         # §3
```

A build or publish attempt on an **unregistered** agent (`registered_at IS NULL`) is rejected, mirroring
the existing `publish.eval_gate` (409) pattern (05, 08 §6). Add one row to the error table (08 §1):

| Exception | HTTP | `code` |
|---|---|---|
| build/publish on an unregistered agent | **409** | `registration.gate` |

`agent.register` joins the audited actions enumerated in 09 §12 (alongside `publish`, `repoint`,
`baseline.freeze`, …), written to `audit_logs` (01 §4.8).

## 3. Similarity scanner (dedup — "reuse first")

The org re-builds the same agent because nothing tells a builder "this already exists." At registration
(and on each new version, below) the platform computes a **signature** and surfaces near-duplicates so
the builder either **reuses** the existing agent or records an audited **override**.

**Signature.** Embed the tuple `{name, description, system_prompt, io_contract}` with a **pinned**
embedding model — a new dependency carrying the **same model-currency caveat as KB embeddings** (04):
changing the model means re-embedding every signature, so the model is pinned per row in `embed_model`
and bumped deliberately, never silently.

```sql
-- agent_signatures — WorkspaceScoped (01 §5 mixin) + RLS (01 §6). Reuses the chunks HNSW pattern (01 §4.4).
-- CANONICAL DDL lives in 01 §4; shown here for context — keep the two in lockstep.
create table agent_signatures (
  id           uuid primary key default gen_random_uuid(),
  workspace_id uuid not null references workspaces(id),
  agent_id     uuid not null references agents(id),
  version_id   uuid not null references agent_versions(id),  -- signature is per-version content
  embedding    vector(1024),                                 -- width = pinned model dim; MUST match 01 §4.9
  embed_model  text not null,                                -- PINNED; changing it = re-embed all signatures (04)
  created_at   timestamptz not null default now(),
  unique (version_id)                                        -- one signature per version (matches 01 §4.9)
);
create index agent_signatures_hnsw on agent_signatures
  using hnsw (embedding vector_cosine_ops) with (m = 16, ef_construction = 64);
create index agent_signatures_ws_agent on agent_signatures (workspace_id, agent_id);
```

> **Pinned-model constraint:** pgvector's `hnsw`/`ivfflat` indexes support at most **2000 dimensions**, so
> the pinned embedding model must emit ≤2000-dim vectors (1024 here). A larger native model (e.g. 3072-dim)
> must be dimension-reduced/truncated to fit, or its signatures stay unindexed — pick a ≤2000-dim model.

**Compare.** pgvector **cosine** against existing signatures in the same workspace/org; the query filters
`workspace_id` explicitly so the HNSW index prunes the namespace (01 §4.4, 09 §7) even though RLS already
gates the row. Overlaps above a threshold are surfaced to the builder:

```python
# services/api/routers/agents.py  → services/runtime/... (embed via the gateway, 02)
async def run_similarity_scan(agent, ctx) -> list["DedupHit"]:
    vec = await embed_signature(agent, model=PINNED_SIG_MODEL)        # {name,description,system_prompt,io_contract}
    hits = await db.execute(                                          # cosine KNN, workspace-namespaced
        select(AgentSignature, cosine(AgentSignature.embedding, vec).label("sim"))
        .where(AgentSignature.workspace_id == ctx.workspace_id,
               AgentSignature.agent_id != agent.id)
        .order_by(text("sim asc")).limit(5))
    return [h for h in hits if h.sim >= SIG_OVERLAP_THRESHOLD]        # tuned on the ~40-agent corpus (§4)
```

Resolution is the builder's, recorded either way: **reuse** the existing agent (abandon the new one), or
proceed and write an audited `dedup.override` (joins 09 §12 / 01 §4.8). A token/channel caller never hits
this path — it is a console, registration-time control.

**Maintenance.** Recompute the signature on **each new `agent_version`** (a trigger on version create,
alongside the snapshot freeze, 01 §4.2) so the signature tracks the agent's current behavior, not its
first draft. Stale signatures silently stop catching duplicates — treat the recompute as part of the
version-create artifact set, like `retrieval_bindings` (08 §2).

## 4. Import / migration

Onboarding the existing hand-wired agents is the whole point of adoption. Model import on the existing
server-side-copy shape of `POST /api/v1/agents/:id/duplicate` (08 §2) — same "create agent + first
version from a config blob" mechanics, different source (an external config instead of a sibling agent):

```
POST /api/v1/agents/import        (S, builder+)        # mirrors the duplicate server-copy shape (08 §2)
```

```python
# services/api/schemas/agents.py — body mirrors the fields a hand-wired agent already has
class AgentImport(BaseModel):
    name: str
    description: str = ""
    provider: str
    model: str
    api_version: str                         # REQUIRED — drives the profile check below (02 §4)
    params: dict = {}                        # temperature/top_p/max_tokens → model_config (01 §4.2)
    system_prompt: str = ""
    io_contract: dict = {}                   # typed inputs + output schema (spec B2.5); best-effort on import
```

```python
# services/api/routers/agents.py
async def import_agent(body: AgentImport, ctx = Depends(require_role("builder"))):
    # HARD dependency on profile coverage — fail fast with a clear error, do not silently guess cost/caps.
    if not profile_exists(body.provider, body.model, body.api_version):     # (02 §4)
        raise InputInvalid("No capability profile for "
                           f"({body.provider}, {body.model}, {body.api_version}); "
                           "add a profile before importing.")               # 400 input.invalid (08 §1)
    agent   = await create_draft_agent(ctx, body)              # status='draft', registered_at=NULL
    version = await create_first_version(agent, body, ctx)     # frozen snapshot (01 §4.2); profile_snapshot resolved
    return AgentOut.from_orm(agent)
```

The imported agent is **not special** — it enters the same funnel as a hand-built one:

> **Import → gate (§2) → scan (§3) → eval → publish.** Import only lands a `draft` + first version; it
> grants no shortcut past the gate. The agent must be **registered** (§2), clear **dedup** (§3), and pass
> an **eval** against a frozen baseline (05) before `publish` (08 §6) will do anything but 409. This is
> the point of adoption: a hand-wired agent becomes *governed* the moment it is imported, not merely
> copied.

**Profile coverage is a hard dependency.** Import validates a capability profile exists for the agent's
`(provider, model, api_version)` (02 §4); if absent it **fails with a clear error** rather than importing
an agent whose cost/caps the platform cannot compute. This surfaces the standing **profile-currency
maintenance gap** (02): the import success rate across the existing fleet is a direct read on how
complete the profile catalog is.

**Bulk path.** For migrating the ~40 existing hand-wired agents, the single-import call is the unit; a
thin batch driver (a worker job, 00 §3 `ingestion`/jobs substrate, 01 §4.8 `jobs`) feeds configs through
`import_agent` one at a time, collecting a per-agent result (`imported` | `profile_missing` |
`dedup_hit`). The `profile_missing` bucket is the migration's punch-list against 02; the `dedup_hit`
bucket is the first real measurement of how much duplication the fleet already carries.

## 5. MCP server governance = policy + egress + audit, not a registration allow-list

MCP governance is **open registration**: **any builder+** may register a workspace-scoped `McpServer`
(spec B7; RBAC reconciled to builder+, 09 / spec B12). There is **deliberately no vetted catalog and no
registration gate for MCP servers** — a server is not pre-approved onto an allow-list before it can be
added. (Decided 2026-10-19; this reverses the demo's earlier "connect only vetted, existing MCP servers,
nobody spins up a new one" framing — see `scope.md`, `demo-transcript.md`.)

Governance is applied **after the fact**, by three machines that already exist, not by a gatekeeper:

- **Per-resource `access_policy`** (09 §5) — who in the workspace can see/use/manage the server, same
  unified `AccessPolicy` as every other resource (public/restricted by role+team, or `private`/owner).
- **Per-server `config.egress` allow-list** (09 §10 / 01 §4.6) — **deny-by-default**; a server can only
  reach the domains/CIDRs explicitly allowed, enforced at the sandbox network boundary (11 §1), so what a
  tool can touch is explicit and bounded regardless of who registered it.
- **Audit** — `mcp.register`, `mcp.discover`, and `tool.bind` join the audited actions in 09 §12
  (`audit_logs`, 01 §4.8), so every server added, every discovery, and every binding onto a version is
  attributable.

This is the inverse of the **agent** registration gate (§2), which **stays**: an *agent* still cannot be
built/published until registered (owner + dedup + eval). The MCP decision only removes a *pre-approval
catalog for servers* — the thing an agent is proven through is its eval gate and its access/egress policy,
not a curated list of blessed MCP endpoints.

> **Shared machinery.** The per-server egress allow-list is the **same deny-by-default egress control**
> reused one layer out for the provider-egress precondition in §1 (and 09 §9.1 / 11 §12) — one mechanism,
> two boundaries (tool/MCP sandbox egress and platform→provider egress).

→ Next: `15-org-governance-reporting.md`.

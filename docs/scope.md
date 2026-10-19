# Scoping Doc — Agent Platform

> An internal platform to build, configure, test, and publish LLM-powered agents.
>
> Fill only the **Initial scoping** section at the start. Everything below it stays blank on
> purpose until evidence (usually from the MVP) tells you how to fill it. A mostly-blank
> template after scoping is the *correct* state.

---

## Initial scoping (fill now)

- **One-line problem statement:**
  Developers and PMs waste time and repeat the same mistakes hand-wiring LLM agents
  (prompt + model config + tools + guardrails), and there's no safe, consistent way to test
  one and hand a working, trustworthy endpoint to another team — *and* when the underlying
  model changes, nobody notices the agent silently got worse.

- **Who is this for?**
  Internal builders at one org: app developers and technical PMs who want an agent behind an
  API without owning the LLM plumbing. **Assumption (flag):** internal tool with a small
  known set of users, not a public product. If this is actually a solo portfolio build, the
  scope narrows further (drop multi-user concerns entirely) — see decision log.

- **Input data:** (exactly what the user provides)
  - Agent config: name, description, provider + model id, generation params (temperature, max
    tokens, etc.), system prompt, guardrail settings.
  - Playground test messages (chat turns) typed by the user.
  - A small **labeled eval set** per agent: example inputs + expected-behavior assertions,
    used as the trusted baseline.

- **Output data:** (exactly what they get back)
  - A live agent response in the in-browser **chat** playground (streamed).
  - On publish: an authenticated **API endpoint + ready-to-paste cURL** for that agent version.
  - An **eval report**: pass/fail vs. the baseline, a drift flag when the model/prompt changes,
    and cost/latency deltas per run.

- **Constraints:** *(defaults below are assumptions — correct them)*
  - Budget (ceiling $ / month): dev/eval model spend assumed **≤ $100/mo** during build; agent
    runtime cost is pass-through to whoever runs the published agent.
  - Latency target (end-to-end): playground **first token < 2s p95** (chat only). (No voice in
    v1, so no sub-second real-time budget to hit.)
  - Privacy / data handling: internal data only; prompts/configs stored in our own DB; no
    customer PII assumed in v1. Secrets (provider keys) never stored in plaintext configs.
  - Platform (web / mobile / CLI): **web app** for the builder/playground; the published
    artifact is a plain HTTP API. MVP de-risking happens as a **CLI/script** first.

- **Success metrics:** (measurable)
  1. Time from "new agent" → "published, callable endpoint" **< 15 min** for a new user.
  2. The eval harness **catches an injected regression**: when we deliberately swap to a
     weaker/cheaper model or degrade the prompt, the report flags it (target: detect ≥ 80% of
     seeded regressions, < 10% false-positive rate on an unchanged agent).
  3. ≥ 1 guardrail and ≥ 1 eval attached to every published agent (process metric).
  4. Playground first-token latency **p95 < 2s**.

- **Edge cases that change the design:**
  - Provider abstraction leaks: tool-calling format, system-prompt handling, streaming, and
    token/cost accounting differ across providers — "seamless switching" may be partly a lie.
    What happens when an agent uses a provider-specific feature and you switch provider?
  - Eval set is tiny or missing — how does the drift report behave with 3 examples vs. 30?
  - Non-deterministic output: the same prompt + model gives different text each run. The eval
    must score *behavior/quality*, not string-equality.
  - Published endpoint outlives the config: someone edits the agent after others depend on the
    endpoint → versioning + pinned versions must be real, not cosmetic.

- **Does AI even make sense here?** (argue against it first; justify if yes)
  Mostly **no** — and that's the key insight. ~90% of this platform is plain software: the
  dashboard, config CRUD, versioning, API-key generation, and cURL export need **no model at
  all** and should not be treated as an AI problem. AI is genuinely required in exactly two
  places: (1) the **runtime** that executes an agent — unavoidable, but it's just provider API
  integration, not novel; and (2) the **eval / drift-detection** module — the one genuinely
  hard, defensible, AI-flavored part, because detecting "the agent quietly got worse after a
  model swap" without a human reading every transcript is a real, unsolved-for-us problem.
  **Verdict:** build the CRUD as boring, reliable software; spend the real engineering on the
  eval/drift core and the provider abstraction.

- **Biggest unknowns → MVP must answer:**
  1. **Can the eval/drift harness reliably detect a meaningful quality/behavior drop when the
     underlying model changes, without a human reading every transcript?** (The risky, novel
     core. If this doesn't work, the platform is just another config UI.)
  2. **Does one provider-agnostic abstraction actually hold across ≥ 2 providers** for system
     prompts, params, streaming, and cost/token accounting — or does "model-agnostic" break the
     moment an agent touches a provider-specific feature?

- **Out of scope for v1 (explicit decisions, not gaps):**
  - **Voice entirely** — STT/TTS streaming, WebRTC/telephony, noise cancellation, signal
    ratios, profanity hardening, call timeouts/greetings. It's a second project; defer until
    the chat core + eval loop are proven. *(This is the single biggest cut.)*
  - **MCP tool integration** and **sub-agent delegation** — powerful, but not needed to prove
    the core loop; they multiply the runtime + security surface.
  - **Cost/spend controls + concurrency limits** — ops hardening, not core value. *(The console
    exposes per-workspace spend/concurrency/timeout knobs in P1, but runtime **enforcement**
    — budget reservation + concurrency semaphores — is P2; see backend/07, backend/01 §4.8.)*
  - **Multi-user auth / RBAC / org management** — single trusted team assumed for v1.
  - **Rich deployment-status dashboard** — v1 gets a simple published/unpublished list, not
    live deployment telemetry.

---

## Product requirements (fill later)

- User stories:
- Non-goals:
- Feedback signals to collect in-product:

## Data (fill later)

- Sources:
- Storage (DB / file / blob):
- Labeling rubric (consistent rules — write them down):
- Test/eval set composition:

## Models (fill as you experiment)

- Stage 1 (agent runtime / execution) — candidates & why:
- Stage 2 (eval / drift scoring — likely an LLM-judge) — candidates & why:
- Keep stages SEPARATE so you can compare and swap each independently.
- Prompt versions & where they live:

## Evaluation (fill later)

- Deterministic tests:
- AI eval set + scoring rubric:
- Trusted baseline:
- CI setup:

## Infrastructure (fill later)

- Env management:
- Secrets handling (.env gitignored):
- Model router / provider abstraction:

## Security & privacy (fill as discovered)

- Provider API keys: encrypted at rest, never in agent config blobs or exported cURL:
- Auth (if any):

## Deployment (fill later)

- Host:
- CI/CD → deploy on green:
- Domain:

## Monitoring (fill later)

- What every model call logs: model, prompt version, input, output, latency, cost, success/fail
- Dashboard:
- Alerts:

## Decision log (append as you go)

| Date | Decision | Why | What changed it |
|------|----------|-----|-----------------|
| 2026-10-04 | Voice cut from v1 | Separate project (streaming STT/TTS, WebRTC, sub-second latency); not needed to prove core value | Initial scoping |
| 2026-10-04 | Chat-only, provider-agnostic agent + eval/drift harness is the v1 core | The only novel/defensible part; rest is CRUD | Initial scoping |
| 2026-10-04 | MCP tools, sub-agents, spend/concurrency controls, multi-user auth deferred | Multiply surface area without proving the core loop | Initial scoping |
| 2026-10-04 | Declined to produce architecture/schema/API now | No evidence yet; that's the MVP's job to earn | /ai-scope process |
| 2026-10-04 | ASSUMPTION: internal multi-user tool (not solo portfolio) | Pending confirmation — flips multi-user scope | — |
| 2026-10-05 | Multi-user auth + RBAC + multi-workspace tenancy pulled INTO scope (was v1 out-of-scope); built in FE mock, shaped to swap to Trellix IAM OIDC | Confirms the 2026-10-04 multi-user assumption; workspace isolation + role gating needed for the console/demo | Frontend `b26cd89` (auth/access model + per-resource access policies) |
| 2026-10-05 | Added a second "Trellix People Ops" (HR/non-AI) demo workspace + per-workspace metrics | Seed world was all SOC/security; a non-technical automator saw "someone else's tool" | Frontend `640453f`; see `people-ops-workspace-plan.md` |
| 2026-10-05 | Added builder-trust UX: ML glossary tooltips, eval onboarding (starter set), eval-failure explanations + remedies, broader agent templates, standalone Playground | Lower the conceptual-vocabulary wall; make the eval/publish gate self-explanatory | Frontend `b8bb3f0`, `4a397e2` |
| 2026-10-05 | Built full-fidelity datasource-connector config (Confluence/S3/Git/Drive, distinct auth shapes) + HTTP-tool config (method/url/auth/headers/schema), FE-mock, ahead of the P2 backend | A connector/tool with no credential or config capture can't be configured at all; proves the registry/credential abstraction before the backend exists. MCP unchanged; backend ingestion/connection stays P2 | Connector registry `connectors.ts` + credential-ref masking `secrets.ts` |
| 2026-10-07 | Added messaging-app **channels** (Slack/Teams/Google Chat) as a distribution surface — FE-mock entity/registry/routes ahead of the P2 inbound adapter. Discord dropped for now | Users want to reach hosted agents from chat apps, not cURL. Chose **federate-to-Okta** (verify webhook → sender email → Okta subject → mint short-lived token): internal employees in one Okta, so no new external-identity class — a federated human is `subjectType user`, Okta groups reuse the existing `teams`. One channel → one workspace → many agents | FE `channels.ts`/`channel-credentials.ts` + `/channels` routes; design `channels-design.md` + `backend/12-channels.md` |
| 2026-10-15 | **Workspaces → "spaces" on org Okta.** Membership is **app-managed** (invite / self-join an `access: open` space; space admin = `admin` role), not an IAM-directory read. Optional per-space backing Okta group may auto-grant membership (not authoritative); role is NOT group-encoded. Per-resource `AccessPolicy` unified FE↔backend and gained a `private`/`owner` tier (Splunk-style). **Reverses** the earlier "workspace = Okta group deferred" (`09 §2.1`) and "membership read-only from IAM" (`frontend-auth-access-plan`) decisions | Models the Google Chat / MS Teams / Splunk pattern the product needs; makes backend docs match the FE and vice-versa | FE `iam.ts`/`access-policy.ts`/`auth.tsx`/`store.ts` + settings/switcher/no-access; docs `09`,`01`,`08`,`frontend-auth-access-plan`,`12`,`13` |
| 2026-10-18 | **P0-1 adoption/enforcement spine = key custody.** The platform becomes the sole holder of provider credentials (per-workspace **BYO superseded** for enforcement; `backend-integration-plan` Part 4 reversed); plus a create-time **registration gate** (`agents.registered_at`, not a new status) and a **similarity/dedup scanner** (`agent_signatures`) + **import** flow. **Honest caveat:** teeth require cloud-IAM centralization + egress control; without it the spine degrades to "visibility + mandate" | A governance platform nobody is forced onto governs nothing; this was the only `net-new` item in the champions map and the weakest point of the pitch | New doc `backend/14`; edits `01`,`02`,`08`,`09` |
| 2026-10-18 | **P0-2 org governance = a platform-admin plane.** A single app-managed deployment-wide `platform_admin` role (one deploy = one org, so **no `org_memberships`**), an **elevated audited cross-tenant read path** (the single `09 §8` isolation-suite exception), and org estate/spend/compliance reports — **metadata-only** for `private`-tier resources | Leadership had no org-wide view; the cross-tenant path was named but unspecified in `09 §7`/`01 §6`; no role spanned workspaces | New doc `backend/15`; edits `01`,`08`,`09` |
| 2026-10-18 | **P0-3 owner notifications.** A `notifications` entity + emit at the drift-transition / eval-fail / budget-threshold / **model-deprecation** points (the last needs a new `availability` field on capability profiles, `02`), deduped to avoid noise; distinct from ops Alertmanager alerts | "Nobody notices the agent got worse" is the founding problem; drift was detected and told no one | New doc `backend/16`; edits `01`,`02`,`05`,`07`,`10` |
| 2026-10-19 | **Next-steps sequence = design-first, then MVP** (user chose over my MVP-first rec); live provider spend **not yet approved** → MVP starts on **cheap/local** models, real 2nd provider added on greenlight. New `docs/mvp-spec.md` defines the de-risking MVP (the two unknowns as pass/fail hypotheses) — the MVP `spec.md` had *assumed* already passed | The design is complete but the two load-bearing unknowns (eval/drift detection, provider abstraction) are still unvalidated; nothing should hit the full build until they are | New `docs/mvp-spec.md`; `spec.md` Part C/F |
| 2026-10-19 | **Spend cap promoted P2 → P1.** The hard per-workspace **monthly** spend cap is now P1-enforced; concurrency caps + per-endpoint budgets stay P2 | The pitch sells "caps" — v1 must actually refuse spend past the ceiling, not just display it | `backend/07 §3`, `01 §4.8`, `08`, `spec.md` |
| 2026-10-19 | **LangGraph licensing RESOLVED** — OSS LangGraph lib (MIT) behind our own FastAPI; **LangGraph Platform not adopted**. Added dep **pinning + upgrade-via-eval-gate + CI license scan** policy | Closes `spec` risk #7; avoids a new per-seat/usage license on a compounding bill; manages LangGraph/LiteLLM churn | `backend/11 §12`, `spec.md` risk #7 |
| 2026-10-19 | **Cost-accuracy + scale hardening.** Fixed the pre-existing `chunks` `vector(3072)`+HNSW bug (pgvector's 2000-dim index cap) via a **halfvec** index + HNSW/IVF scale-ceiling guidance; added the **profile-currency maintenance process** (owner/cadence/CI staleness/reconciliation); added the **platform's own COGS model** (eval + infra, formula-not-total) | Three review gaps: silent cost drift from stale profiles, an un-indexable 3072-dim vector, and a cost-governance product with no cost model of itself | `backend/01 §4.4`, `04`, `02 §4.2`, `07 §12` |
| 2026-10-19 | **MCP/tools backend design closeout.** Pinned the **MCP client** (official `mcp` SDK) + **sandbox runtime** (gVisor + egress allow-list proxy); added an **isolated `sandbox` unit** to the topology; designed the **human-approval gate** for destructive tools (`tool_risk_tier` / `tool_approvals` / runtime pause-resume); added **`subagent_configs`** DDL + depth/fan-out caps (`runs.parent_run_id`); added **tool/MCP dry-run `test-invoke`**; structured **`config.egress`** schema; **audited tool binding** (`tool.bind`). **⚠️ Reversal 1: MCP/tool RBAC reconciled DOWN to builder+** (reverses spec B12 "publisher/admin"; only provider-key mgmt stays admin). **⚠️ Reversal 2: MCP governance = open registration + policy/egress/audit** (no vetted catalog) → softened the demo "vetted MCP / nobody spins up a new one" wording | P2 MCP/sub-agent surface was designed only at the spec level; closing it out needed concrete deps, an isolation boundary, the approval/sub-agent DDL, and two RBAC/governance stances that contradicted earlier text | `backend/00 §2/§4/§7`, `01 §3/§4`, `03`, `08`, `09 §10`, `10 §4/§5`, `11 §1/§2/§12`, `14 §5`, `16 §2.6`, `spec.md B7/B12/D`, `demo-transcript.md` |

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

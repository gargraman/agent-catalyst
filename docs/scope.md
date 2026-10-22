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
| 2026-10-20 | **Workspace self-serve CRUD + reversible Inactive state (FE).** Workspace identity promoted from the static `iam.ts` const into store state so it's mutable: **create** (switcher dialog; creator → admin; seeds baseline config + a starter agent/KB/tool + zeroed metrics), **edit** identity (name/access_mode/okta_group), a reversible **Inactive** state (read-only freeze enforced at one choke point — `auth.tsx can()`; Activate/Delete bypass the freeze), and **hard delete** (cascade-removes every scoped resource). **⚠️ Divergence from backend design:** self-serve **create** and **hard delete** are **mock-only** — `backend/08` has no `POST /v1/workspaces` (workspaces are org-provisioned) and models delete as archive (soft); reactivate needs a new endpoint to complement `POST …/archive`. Identity edit (name/access_mode) is contract-faithful (`PATCH …`). `members`/`invites` (global roster) and `providerKeys` (org-level) are intentionally left out of the delete cascade | A tenant model you can't create/retire from the console isn't manageable; the hollow `archivedAt` flag had no teeth. Mock-only create/hard-delete accepted to make the demo self-serve | FE `iam.ts`/`auth.tsx`/`store.ts`/`data.ts` + `app-shell.tsx` switcher, `settings` Workspace tab, `playground.tsx`/`channels/[id]`; docs `backend-integration-map.md`, `backend/08` |
| 2026-10-20 | **Skills added as a first-class, version-bindable capability** — a reusable, versioned package = **instruction fragment** (injected into the prompt like `system_prompt`) + **bundled reference files** (stored like KB documents in MinIO). Peer to KBs/Tools but **does NOT execute code**: no risk tier, no approval gate, no test-invoke, no sandbox/egress — so **not** an SSRF/exfil vector. Only new surface = **prompt-injection via instruction text** + **package provenance/signing** (signed at publish, verified at bind; only signed skills bindable; no credentials). Governance = **open registration + signing** (mirrors MCP, 14 §5); RBAC `skill.manage` at **builder+**; per-resource `access_policy`, access-checked at invoke. **Load-bearing invariant:** a bound skill's content hash folds into `agent_versions.snapshot_hash` so a skill edit busts the eval verdict cache + re-arms the publish gate. **P2** (by analogy to tools/MCP). FE mock shipped; docs document the backend contract | FE "Skills" capability was mocked and needed a first-class backend contract peer to KBs/Tools, without inheriting the tool-execution security/approval surface it doesn't have | `backend/00 §3/§4/§7`, `01 §2/§3/§4.2/§4.4/§5/§6/§8`, `03 §4`, `05 §5/§6`, `08 §2/§3`, `09 §4/§5/§8/§9/§10/§12`, `10 §1`, `11 §1/§12`, `14 §6`, `16 §2`, `spec.md A0/A1§3/B3/C`, `backend-integration-map.md`, `backend-integration-plan.md` |
| 2026-10-20 | **⚠️ Workspace access model → Okta-GROUP-driven (REVERSES 2026-10-15).** Membership + role are no longer an app-managed roster; each workspace **binds ONE authoritative Okta group** (`Workspace.oktaGroup`) conferring a single base role `memberRole ∈ {viewer,builder,publisher}`. A deployment-wide **platform-admin group** (`PLATFORM_ADMIN_GROUP`, aligns with `backend/15 platform_admins`) is admin in every workspace and the **only** principal that can create/delete/(de)activate/edit workspaces. **Removed:** invites, self-join, `access: open/restricted`, `memberships` roster, the create→admin overlay. **Visibility = group membership** (platform-admins see all); the Members tab is now a **read-only** roster projected from the group. **RBAC consequence:** the 4-role ladder flattens to {memberRole, admin(=platform)} per workspace — publish/tool-approve/API-tokens work for members only when `memberRole=publisher`; all admin-tier governance is platform-admin-only ("memberRole is the only knob"). Invoke/access-policy layer unaffected (principal-based). `teams` (functional Okta groups) unchanged | Product owner wants membership/role sourced from Okta, not an in-app roster; simpler, IdP-authoritative, matches the already-designed `platform_admins` plane. Accepted tradeoff: zero in-app self-service for membership | FE `iam.ts`/`auth.tsx`/`store.ts`/`data.ts` + `app-shell.tsx` (switcher + platform-admin preview), `settings` (Workspace tab + read-only Members), `no-access.tsx`; docs `09`, `01`, `08`, `16`, `frontend-auth-access-plan.md`, `people-ops-workspace-plan.md`, `backend-integration-map.md` |
| 2026-10-22 | **⚠️ Auth model → CLAIM-based; three planes; admin is a mutating superuser (REVERSES 2026-10-20 one-group-per-workspace, which REVERSED 2026-10-15).** Workspace membership + role are **per-workspace Okta group claims** in the OIDC token — `ap-<group_prefix>-builder` / `ap-<group_prefix>-publisher` — not a single `okta_group`+`member_role` and not an app roster. **Four claim kinds:** per-workspace `builder < publisher`; the app-wide `admin` (the `ap-platform-admin` claim, the *one* claim unique to the application); and the caller role `executer` (execute-only; cURL + channel unified into one **Caller** plane). **NO viewer.** The 4 auth planes collapse to **3** (Console · Caller · Admin). **admin now MUTATES** — it does everything builder/publisher can in *every* workspace, owns all governance (keys, eval policy, baseline, access policy, limits) **and workspace CRUD**, plus the elevated org-wide read (`org.report.read`, metadata-only for `private`) — this **reverses** `15`'s "platform-admin plane is read-only, mutates nothing". **Removed:** `memberships`/`workspace_invites`/`platform_admins` tables, `member_role`/`workspace_access`/`invite_status` enums, `access_mode`. `workspaces.okta_group`+`member_role` → `workspaces.group_prefix`; new `workspace_role` enum = {builder,publisher}. `teams` (functional Okta groups) unchanged | Product owner wants role conferred by *per-workspace* Okta group claims (a user can be publisher in XDR, builder in Labs) rather than one group → one role; collapses caller-token + channel into one execute-only identity; folds the separate read-only platform-admin into a single app-wide admin claim so "govern a space" and "read every tenant" are one audited claim. Accepted tradeoff: admin is a broad superuser — mitigated by a small, fully-audited admin set and a separate `org.report.content_read` gate for private content | docs `09`, `01` (done); **propagated** `15`, `08`, `16`, `12`; **FE migration PENDING** (`permissions.ts` drop `viewer`, `iam.ts` claim resolution, `data.ts`/GRC seed) — see `09 §1`, `frontend-auth-access-plan.md` |
| 2026-10-22 (rev 2) | **⚠️ IdP-AGNOSTIC adapter layer + three amendments to rev 1.** (1) **Identity is pluggable** — a new `IdentityProvider` adapter boundary (new doc `18`) keeps the authz core (`Principal`, `can_use`, RLS, `RANK`) vendor-neutral; all IdP specifics (token verify, group→role mapping, `resolve_subject`/`groups_for`/`members_of`, Entra OBO) move behind adapters (generic-OIDC + Okta + Entra in P1; Ping/SAML deferred; custom documented). `workspaces.group_prefix` → **`workspaces.idp_binding`** (per-workspace role→native-group map; `group_prefix` kept only as Okta-adapter sugar). The `ap-<prefix>-*` naming is now the *Okta adapter's* convention, not the universal rule (Azure emits group GUIDs + truncates claims → needs Graph). (2) **`viewer` RE-ADDED** — rev 1's "no viewer" removed read-only console access, stranding the auditor/compliance persona (GRC); `workspace_role` enum = {viewer,builder,publisher}, `RANK` restored. (3) **Notifier recipient enumeration RESOLVED** — owner-only floor (always) + fan-out via a `group_members` **read-model cache** (not authz SoR) populated by the adapter's `members_of` or login-upsert; degrades to the floor, never silent (`16`, `18 §4`). **Honest concession:** pluggability re-introduces a small app-side *mapping config* (`idp_binding`) + an adapter API client — the pure "IdP token is the only SoR, nothing stored" story only holds when we control the IdP's group naming, which we don't across vendors | We can never depend on one IdP (customers run Okta/Ping/Entra/Cognito/custom); same adapter pattern the platform already uses for LLM transport (`ProviderAdapter`) and secrets (`KeyVault`). viewer re-add: the read-only auditor is a real product tier rev 1 dropped without recorded rationale. Enumeration: `members_of` isn't available on every IdP, so a cache + floor is the only portable answer | new doc `18`; edits `09`,`01`,`08`,`12`,`15`,`16` (done); **FE migration still PENDING** (`iam.ts` adapter-shaped `roleFor` + `idpBinding`; keep `viewer`) |
| 2026-10-22 (rev 3) | **KB content is LIVE & INDEPENDENT of the agent version (resolves review-fixes C3's shape).** The agent version pins the **binding** (which KB + `retrieval_config` + `embedding_model`), **not** the KB's content; a KB refresh (periodic/manual) updates chunks in place and does **not** bump the agent version — the two events are independent. **Dropped:** the false `04 §9` "runs on a pinned `index_version` until an eval-gated re-point" claim; C3 options A/B (chunk/index versioning, copy-on-write, N-version storage + GC) **and** option C's freeze — re-ingest of a bound KB is allowed and goes live immediately. `snapshot_hash` covers the **binding** only, **not** KB content. **Keep regardless (robustness):** ingest atomicity/rollback; dedup keyed on `(content_hash, embedding_model, chunk_config)` so an embedding-model change actually re-embeds. **Deliberate asymmetry:** skill content **is** pinned into `snapshot_hash` (instructions = pinned); KB content is **not** (reference data = live). **⚠️ PARKED (revisit):** whether a KB content refresh carries an eval/drift signal (flag `needsReEval` + notify on regression vs. fire-and-forget), and therefore whether `kb_index_version` folds into `snapshot_hash`/busts the verdict cache. Consequence accepted meanwhile: a refresh can change a live agent's answers with no drift signal | Product intent: a KB is a living reference source (docs refresh on their own cadence); forcing a re-version/re-eval per refresh is wrong for the knowledge-ops use case. Dropping chunk-versioning removes large storage/GC/pgvector-HNSW complexity. Eval-signal-on-refresh deferred to revisit | `04 §9`, `05` (RAG snapshot-hash note → provisional; skills asymmetry noted), `review-fixes.md` C3 (done); **parked:** eval signal + `kb_index_version`→`snapshot_hash` |
| 2026-10-22 (rev 4) | **Closed the remaining fix-before-building items C2, C4, H1 (design-level).** **C4 — per-user-MCP secrets ratified to a per-USER DEK** (least-privilege, matches the per-user audit goal), resolving the `09 §9` ↔ `17 §3` contradiction (tool/connector/channel + shared-MCP stay per-workspace DEK; provider keys stay the platform pool). Org-plane hardened: runs on a **separate `platform_report` DB connection** with the general app role holding **no `EXECUTE`** on `org_report_*`, `enrich_estate` routed through the definer fns; **worker-GUC** set/reset per job; new isolation-suite cases (worker-GUC, org-plane DB boundary, per-user-DEK cross-tenant **and** cross-user). **C2 — eval-gate integrity:** publish gate wrapped in a **per-version advisory lock** (closes the latest-eval/latest-trigger TOCTOU) + a **CI property test** that every `DRIFT_TRIGGERS` field folds into `snapshot_hash` (excludes `kb_index_version`, parked per C3). **H1 — channel federation:** require **`email_verified`** + **corporate-domain bind** (reject guest/Slack-Connect email collisions), email documented non-authoritative + hardened-tenant precondition, **inbound event-id idempotency** + dead-letter; Teams-OBO side-issue folded into the IdP adapter (`exchange_obo`, uniform `teams`). **All five review-fixes items now resolved at the design level; code unbuilt** | The five fix-before-building items had to clear before committing to the build; C2/H1 had no open decisions, C4 needed only the DEK ratification | `09 §8/§9/§9.1`, `15 §2`, `05 §5`, `12 §4`, `review-fixes.md` C2/C4/H1 (done) |
| 2026-10-22 (rev 5) | **FE migrated to the claim-based, IdP-agnostic model** (closes the standing "FE migration PENDING" from rev 1–4). `iam.ts`: `Workspace.{oktaGroup,memberRole}` → `groupPrefix` + an `idpBinding(ws)` helper (Okta-adapter convention `ap-<prefix>-<role>`); `roleFor` resolves the highest per-workspace role claim (admin from the platform-admin claim), `viewer` retained; `MOCK_PROFILE` now carries per-workspace claims (publisher@XDR, viewer@GRC, builder@Labs/People). Create/edit-workspace (`app-shell`, `settings`) take `groupPrefix`; Members tab projects the signed-in user's resolved role. `npm run build` ✓ (lint: only 3 pre-existing errors in untouched files). **Deferred:** `runtime-access.ts` `executer` caller role (widening the caller `role` cascades into `access-policy.ts` typing; left a `// TODO(executer)`) — preview-only, real check is server-side | The FE was the last artifact on the superseded model (review-v2 finding #1); migrating it keeps FE ↔ docs consistent | FE `iam.ts`/`store.ts`/`auth.tsx`/`app-shell.tsx`/`settings`/`data.ts` (done); **deferred:** `runtime-access.ts` executer |
| 2026-10-22 (rev 6) | **`executer` caller role landed + cross-doc consistency pass (closes C1 end-to-end).** FE caller planes (token + channel) now resolve to the execute-only **`executer`** role, not a console tier: new `CallerRole = Role \| "executer"` in `access-policy.ts` (`matches()` widened), `runtime-access.ts` `decodeToken`/channel arm return `executer`, channel `RunCtx` drops the console-role passthrough; `npm run build` ✓. Also swept the half-applied leftovers the resync review flagged: `15 §2.3` "workspace admin" → app-wide admin; `backend-integration-map`/`-plan` auth rows (roster/invite/open-restricted → claim model); `spec` B12 role list; `01` "Space Manager" → admin; `iam.ts` platform_admins-table refs → admin claim; `user-flows`/`people-ops` term drift; `architecture-review-v2` resolution banner + §13 answered | Last substantive C1 residual (the rev 5 deferral) + the consistency leftovers surfaced by the resync review | FE `access-policy.ts`/`runtime-access.ts`/`channels/[id]`; docs `15`,`backend-integration-map`,`backend-integration-plan`,`spec`,`01`,`user-flows`,`people-ops`,`review-fixes`,`architecture-review-v2` (done) |
| 2026-10-23 | **Caller-vs-`restricted`-policy semantics = B/D (teams gate callers).** A `/code-review` pass found that after the `executer` change a `restricted` policy naming any `useRole` denied **all** callers (channel/token) even on a matching team (`matches()` = roleOk && teamOk; `executer` ∉ console roles) — a fail-closed footgun that also contradicted the documented channel model "channel `okta_group` ∩ agent `accessPolicy`". **Decision:** `useRoles` gate **console** principals only; an `executer` caller is gated on **`useTeams` alone** (a role-only `restricted` policy still denies callers — fail closed; a team-named one admits a caller on a matching team). Chosen over option A (role-restricted = console-only; strict but would need a channel-doc amendment + an editor warning) because B/D is the only option consistent with the already-written channel access model and keeps channel-published restricted agents usable. **Related clarification (requirement, not yet built):** the "open to all for execution + see-your-own-data" HR/ops archetype = `workspace`/broad visibility (invoke) **+** per-user data scoping at the tool/retrieval layer — per-user tool identity (`17`) and/or per-user RAG doc-ACLs (`04 §10`); this is a distinct layer from the access policy. Flagged: `17` is P2+/proposed (not eval-gatable, no published-API path) and per-user RAG ACLs are undesigned — both load-bearing for this archetype. Also open: whether "open to all" needs a cross-workspace **`org-public`** visibility tier (channel path works today) | The `executer` refactor surfaced a model gap in caller-vs-role-policy; B/D keeps FE==server and matches the channel design | FE `access-policy.ts` `matches()`; docs `09 §5` `_matches` + restricted prose (done); **flagged/not built:** `17` pull-forward, per-user RAG ACL, `org-public` tier |

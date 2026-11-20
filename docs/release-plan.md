# Release plan — v1 go-to-market and future versions

**Status:** owner-approved 2026-11-15 (assumptions A1–A3 confirmed; R1 option (a) chosen). **Owner decision (2026-11-15):** ship v1 to external
customers. v1 does not need every feature or every scenario handled, but it must have the obvious,
relevant ones. Everything else is classified Nice (v1.x) or deferred to a named future version.

This reverses the 2026-10-31 "internal pilot first" release boundary (see the `docs/scope.md` decision
log). Build status per family stays in `docs/backend-integration-status.md`; this doc only says what
must ship *when*.

## How features were classified

A feature is **Must (v1)** if a paying customer would hit its absence in the first week, or if its
absence is a security, data-correctness or billing-trust failure. That covers:
- the core loop: build → configure → test → evaluate → publish → call;
- isolation, auth and secrets;
- not overspending;
- operating it in prod.

**Nice (v1.x)** means valuable, but v1 is sellable without it. **Future (v2+)** means a new product
surface or an enterprise/scale concern.

Assumptions (confirmed by owner 2026-11-15):
- **A1:** v1 is hosted by us in our AWS account, multi-tenant, with workspace = tenant. A dedicated
  per-customer deployment is not offered in v1.
- **A2:** v1 agents are chat and knowledge (RAG) agents. Agents that execute tools are not in v1
  (see Risk R1).
- **A3:** customers sign in through their own OIDC IdP via the pluggable adapter. There is no
  username/password login.

## v1 — Must (go-to-market)

| # | Feature | Backend | FE | Gap to close for v1 |
|---|---|---|---|---|
| 1 | Agent create/configure/version (core loop) | BUILT | partial live | Wire PATCH, lifecycle and versions (M12) |
| 2 | Playground invoke (SSE) | BUILT | MOCK | Wire the stream render (M12) |
| 3 | Publish + runtime tokens + published invoke API | BUILT | MOCK | Wire mint/list/revoke; add customer-facing API docs + cURL export |
| 4 | Eval runs + drift verdict (the defensible core) | BUILT | MOCK | Wire the UI; ship a **starter eval set** (none is authored today, so the drift claim is unproven) |
| 5 | Knowledge bases + document upload + retrieval with citations (M7 Must) | BUILT | MOCK | Wire the UI; **live Bedrock verification** (owner-gated) |
| 6 | Injection containment on retrieved content | BUILT | n/a | **Held-out corpus** for the SC-004 gate (owner-gated) |
| 7 | Provider resilience: retry, breaker, live/batch bulkhead (025 US1–US3) | in progress | n/a | Finish 025 |
| 8 | Binding integrity: FKs, immutability, CHECKs (025 US4) | not built | n/a | Finish 025 |
| 9 | OIDC console login + session + CSRF | BUILT | MOCK | Wire the login flow (M12) |
| 10 | RBAC (4 roles) + per-resource access policy + RLS tenancy | BUILT | MOCK | Replace the client-side mock with server roles |
| 11 | Members roster + IdP group sync | BUILT | MOCK | Wire |
| 12 | Provider-key custody (Secrets Manager) | BUILT | MOCK | Wire capture/rotate/revoke |
| 13 | Monthly spend cap + spend view | BUILT | MOCK | Wire |
| 14 | Rate limiting + immediate revocation (deny-set) | BUILT | n/a | — |
| 15 | Run history + dashboard metrics | BUILT | MOCK | Wire |
| 16 | Workspace settings + per-call content-logging opt-in | BUILT | MOCK | Wire; decide the 023 prod fingerprint-key guard |
| 17 | Workspace provisioning by the operator (script or admin endpoint) | not built | — | Minimal operator path to onboard a customer; self-serve create is Nice |
| 18 | **Production deployment**: IaC, TLS, managed Postgres/Redis, backups, migrations on deploy | not built (dev compose only) | — | Hard blocker. No Terraform/Helm exists today |
| 19 | Ops minimum: Prometheus metrics + alerts (breaker open, spend, error rate), log retention | metrics BUILT | n/a | Alert rules + a runbook |
| 20 | Customer offboarding: workspace data export/delete procedure | not built | — | A documented, tested procedure (contractual need) |
| 21 | Security hygiene before launch: rotate the exposed dev credential, secret scan in CI, org security sign-off for customer data → Bedrock | partial | — | Owner actions |

**Biggest gaps (be honest):** the backend is mostly built, but v1 is blocked by:
- **M12 frontend wiring:** 4 of 257 seams are live.
- **Production deployment:** nothing exists beyond dev compose.
- **025 hardening:** in progress.
- **Three owner-gated items:** Bedrock live check, held-out corpus, security sign-off.

## v1.x — Nice (after launch, prioritized)

| Feature | Why it waits | Notes |
|---|---|---|
| Tool execution (HTTP tools) + sandbox/egress + approval gate | Large security surface; v1 sells on chat + RAG + evals | Registry CRUD is built, the execution path is not. **First v1.x item**, see R1 |
| Owner notifications: eval-fail, drift, budget threshold (M10 minimal) | Drift detected but told to no one is the founding problem, but dashboards cover v1 | In-app first, email later |
| Retrieval-as-a-tool (024 US4) | Needs resilience first (now done); agentic loop | — |
| `retrieved_source` eval assertion + KB-refresh disclosure (024 US5) | Improves RAG eval fidelity | — |
| Self-serve workspace create, reactivate, hard delete | Operator provisioning covers v1 | FE mock exists |
| `cost_basis` field on runs (025 Nice) | OpenAPI description discloses the basis today | — |
| Presigned direct-to-S3 upload | Multipart through the API is fine at v1 sizes | — |
| Model-deprecation `outdated` view | Signal exists; a view only | — |
| Workspace concurrency caps / per-endpoint budgets | Monthly cap covers v1 trust | — |

## Future versions (v2+)

| Version theme | Features |
|---|---|
| **v2: Agentic** | MCP servers + tools execution (gVisor sandbox), Skills (signed packages), LangGraph orchestration, sub-agents (depth/fan-out caps), memory, response cache |
| **v2: Distribution** | Messaging channels (Slack / Teams / Google Chat, federate-to-IdP) |
| **v2: Data** | Datasource connectors (Confluence / S3 / Git / Drive), scheduled KB refresh with a drift signal, per-user RAG doc ACLs and per-user tool identity ("see your own data") |
| **v3: Enterprise governance** | Adoption/enforcement spine (M8: registration gate, dedup scanner, import), org-wide governance reporting (M9 platform-admin plane), SCIM / automated offboarding, Ping/SAML adapters, dedicated per-customer deployments |
| **v3: Scale + analytics** | Split interactive/batch workers (M11), distributed bulkhead across replicas, Langfuse v3 + ClickHouse analytics, commercial billing/metering |
| **Out of scope** | Voice (separate project) |

## Risks to the v1 boundary

- **R1: no tools in v1.** Many buyers expect agents that act, not only answer. Options:
  - **(a) — DECIDED by owner 2026-11-15.** Ship v1 as chat + RAG + evals and land HTTP tools in v1.1 (the security surface
    needs the sandbox/egress work done properly).
  - (b) Pull HTTP tools into v1. This adds roughly a milestone and the full approval/egress path.
- **R2: unproven drift claim.** Drift detection has never run against real prompt/model variation. Do
  not market "drift-aware" until the starter eval set has run against at least one real model change.
- **R3: single-replica limits.** The bulkhead and local breaker fallback are per-process. That is
  acceptable at v1 scale and must be stated in the SLA.

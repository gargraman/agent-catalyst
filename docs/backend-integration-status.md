# Backend Integration Status (mock ↔ real)

**Last reconciled against:** feature 018 (M5 caller-token mint + deny-set) backend, branch `feat/backend-m0-foundation` · **Date:** 2026-11-12
(history: 006 M4-remainder → 007 trust boundary → 008 RBAC enforcement → 009 spend cap → 010 observability → 012 IdP authN → 013 access policy → 014 provider-key custody → 016 rate limiting → 017 SSRF egress guard (built unwired) → 018 caller-token mint + deny-set + published invoke plane)
**Total `@backend` seams** (`grep -rn "@backend" frontend/src | wc -l`): **257**

This tracker records the honest state of the mock→real migration on **two independent axes**:

- **Backend endpoint** — does a real, tested API endpoint exist? (BUILT / DEFERRED)
- **Frontend wired** — has the console hook been swapped from the mock store to that endpoint? (MOCK / REAL)

> ⚠️ **Current frontend-wired state: 0 / 257 seams are REAL.** The backend endpoints below are built and tested,
> but the frontend console still calls the in-memory mock store. Wiring requires generating the typed client
> (`npm run gen:api`) and swapping hooks. The OpenAPI 3.1 contract is emitted to `frontend/openapi.json` and the
> `gen:api` script + `openapi-typescript` devDep are wired, so the wiring is a mechanical step.
> Until a seam is swapped + build-verified, it may not be described as implemented in the console (P1/P9).
>
> **Toolchain (updated 2026-11-07):** Node/npm **is** available via `nvm use 24` (Node v24.19.0 / npm 11.17.0);
> `node_modules` is installed (`next@16.3.8`). `npm run lint` and `npm run build` pass clean today (the earlier
> "Node unavailable" note was wrong — nvm just wasn't loaded in the non-interactive shell). So FE wiring is
> **no longer blocked** — it is simply **not yet done** (0/257). The 009 FE contract-sync edits (`WorkspaceSpend`
> type + `useWorkspaceSpend()` seam) are now **build-verified**.
>
> **Phase 5 contract proof — agents family (feature 011, 2026-11-07):** the typed client (`gen:api` →
> `src/lib/api/schema.ts`), the mock→dev-auth bridge + fetch client (`client.ts`), and the AgentOut↔Agent
> adapters (`adapters.ts`) are **built + proven end-to-end** against the real backend (list→create→list round
> trip via the actual client code, tenant-scoped). **FE-wired count stays 0/257** — the in-browser agents view
> is **not** swapped yet: it is gated on **finding F-D** (the console workspace registry uses slug ids while the
> backend scopes by UUID — the switcher must be backed by the real API first). See
> `specs/011-fe-contract-proof/findings.md` (F-A thin AgentOut projection, F-B io_contract, F-C provider casing,
> F-D workspace id). No seam is claimed wired.

## By resource family

| Family | Backend endpoint | FE wired | Endpoints / reason |
|---|---|---|---|
| **agents** (CRUD, versions) | ✅ BUILT (M4a) | ⬜ MOCK | `POST/GET/LIST/PATCH /api/v1/agents`, `POST …/versions` |
| **invoke** (SSE) | ✅ BUILT (M4a) | ⬜ MOCK | `POST /api/v1/agents/:id/invoke` |
| **eval-runs** | ✅ BUILT (M4a) | ⬜ MOCK | `POST /api/v1/agents/:id/eval-runs` (inline) |
| **publish** (advisory) | ✅ BUILT (M4a) | ⬜ MOCK | `POST /api/v1/agents/:id/publish` — advisory divergence (verdict, not blocked) |
| **tools** (HTTP) | ✅ BUILT (006/US1) | ⬜ MOCK | `/api/v1/tools` CRUD + archive/restore |
| **mcp** (servers + tools) | ✅ BUILT (006/US1) | ⬜ MOCK | `/api/v1/mcp-servers[/:id[/tools]]`, `/api/v1/mcp-tools/:id` |
| **skills** | ✅ BUILT (006/US1) | ⬜ MOCK | `/api/v1/skills` CRUD + publish (unsigned stub) + archive/restore |
| **knowledge-bases** (metadata) | ✅ BUILT (006/US3) | ⬜ MOCK | `/api/v1/knowledge-bases` CRUD + archive/restore |
| **ingestion-jobs** | 🟡 DEFERRED (M7) | ⬜ MOCK | `GET /api/v1/ingestion-jobs` returns honest empty; real ingestion is M7 |
| **workspace settings/limits/eval-policy** | ✅ BUILT (006/US4) | ⬜ MOCK | `GET/PATCH /api/v1/workspace/settings`, `PATCH /api/v1/workspace` |
| **members** (roster) | ✅ BUILT (006/US4) | ⬜ MOCK | `/api/v1/members` CRUD — role stored **and now enforced** (RBAC, 008/M5); member **teams** persisted + surfaced (migration 0006) and consumed by the access policy (013) + roster display |
| **runs** (history) | ✅ BUILT (006/US5) | ⬜ MOCK | `GET /api/v1/runs[/:id]` |
| **metrics** (dashboard) | ✅ BUILT (006/US5) | ⬜ MOCK | `GET /api/v1/metrics/{dashboard,runs,evals}` |
| **spend cap** (durable monthly) | ✅ BUILT (009/M6) | ⬜ MOCK | `GET /api/v1/workspace/spend` (MTD vs cap); enforced on invoke + eval-runs — `402 budget.exceeded` over cap, durable (Postgres ledger, F7) |
| **run observability** (correlation + metrics) | ✅ BUILT (010/M6) | ⬜ n/a (ops) | inbound `traceparent`/`X-Correlation-Id` honored → run `correlation_id` + echoed header (F13); `GET /metrics` Prometheus `ac_runs_total{status,source}` + `ac_requests_refused_total{code}` (F12, unauthenticated ops endpoint). Langfuse/ClickHouse deferred |
| **provider keys** | ✅ BUILT (014/M5) | ⬜ MOCK | `/api/v1/provider-keys` capture (write-once) · list (masked-only) · `:id/rotate` · `:id/revoke` (tombstone); `key.manage` (admin) + RLS, no per-resource policy. Raw secret custodied in a **pluggable secrets backend** (`api/secrets/`: `local` for tests, `aws` AWS Secrets Manager / LocalStack) — **reverses the Vault-Transit design** (see `docs/scope.md` 2026-11-11). Resolve-at-invoke replaces the `PROVIDER_API_KEY` env var; fail-closed `config_error`. `services/runtime/` stays boto3-free (P6) |
| **runtime tokens** (mint + deny-set) | ✅ BUILT (018/M5) | ⬜ MOCK | caller-token plane: `POST/GET /api/v1/agents/:id/tokens` (mint `agent.publish`, raw secret shown **once**; list masked) · `DELETE /api/v1/tokens/:id` (revoke) · `POST /api/v1/org/revoke-user` (`member.manage`). Secret stored as sha256+prefix (migration **0008** `api_tokens`, RLS + `api_token_resolve` **SECURITY DEFINER** for the cross-tenant auth lookup). **Published invoke plane** `POST /v1/agents/:id/invoke` resolves the caller token → `role=executer` and shares the SAME `_run_invoke` path as console (P6). Redis **deny-set** (fail-**closed**, inverse of 016's limiter) cuts revoked subjects/tokens on both planes incl. **in-flight stream re-check**; per-**token** rate-limit key `rl:token:{id}`. Durable `audit_logs` row deferred (structured log for now) |
| **SSRF / egress guard** (F19) | ✅ BUILT unwired (017/M5) | ⬜ n/a (no tool-exec path yet) | pure validator `api/egress.py`: per-tool allow-list → deny-by-default → resolve → block private/loopback/link-local/multicast/ULA/metadata + operator extra-deny CIDRs → DNS-rebind/redirect re-validation; `EgressBlocked`→502 `egress.blocked`; `ac_egress_blocked_total`. **Built unwired by design** — no tool-execution path exists in P1 (that's P2); documented integration seam |
| **rate limiting** (F5) | ✅ BUILT (016/M5) | ⬜ MOCK | per-identity **sliding-window** limiter (`api/ratelimit.py`, ZSET log in one MULTI/EXEC, no Lua) via `rate_limit(route_class)` dep on all 32 gated endpoints; keyed `rl:prin:{subject}:{route_class}` (per-**token** invoke key → 018). Limits table in settings; 429 `rate.limited` + `Retry-After`/`X-RateLimit-*`; **fails open + alert** on Redis down (availability control — deny-set/spend fail *closed*) |
| **tool approvals** | 🟡 DEFERRED (M5/P2) | ⬜ MOCK | approval workflow deferred |
| **RBAC enforcement** (4-role) | ✅ BUILT (008/M5) | ⬜ MOCK | `require_permission` on all 47 workspace routes; deny-by-default (coverage test); fail-closed unknown role; 403-before-404; composes with membership + RLS |
| **access policy** (per-resource) | ✅ BUILT (013/M5) | ⬜ MOCK | per-resource visibility enforced fail-closed on top of RBAC+membership+RLS (constitution P3): tiers `workspace`/`restricted`(role AND team, separate USE/MANAGE lists)/`private`(owner+admin); USE gate filters lists + guards get/invoke/eval-run with **404 no-leak** (P5), MANAGE gate guards edit/publish/archive with **404-then-403**; executer caller gated on TEAMS alone; admin bypass; empty-restricted denies all. Mirrors `frontend/src/lib/access-policy.ts` (backend is now source of truth). Teams sourced from the members store under RLS (`membership.teams_for`) |
| **IdP bearer authN** (pluggable) | ✅ BUILT (012/M5) | ⬜ n/a (caller plane) | real bearer-JWT verification behind a vendor-pluggable seam (`api/auth/`: `dev` + `trellix-iam` providers, config-selected); sig+iss+aud+exp+alg, JWKS fetch/cache, fail-closed 401, no token in logs. **AuthN only** — AC role now sourced from the `members` store under RLS (not the token). Dev-header path gated off in prod. Live Trellix-IAM token verified end-to-end (opt-in). **Token mint + deny-set now BUILT (018 — see runtime tokens row).** **Deferred:** console user-login (auth-code/OIDC) flow (019) + IdP group sync (020) |
| **IdP user provisioning** | 🟡 DEFERRED (M5) | ⬜ MOCK | console-login (auth-code/OIDC) flow + IdP group→member/team sync still deferred (per-resource access policy itself is now BUILT — see row above) |
| **channels** | 🟡 DEFERRED (P2) | ⬜ MOCK | messaging channels post-v1.0 |
| **workspace create/delete (root)** | 🟡 DEFERRED | ⬜ MOCK | platform-admin plane out of scope |

## Totals

- **Backend endpoints/controls BUILT:** agents, invoke (SSE), eval, publish (advisory), tools, mcp, skills, KB, workspace settings, members (+ teams), runs, metrics (+ honest-deferred ingestion stub); **RBAC enforcement (008)**; **durable monthly spend cap + `GET /workspace/spend` (009)**; **correlation ingestion + `GET /metrics` (010)**; **pluggable IdP bearer authN + membership-sourced authZ (012)**; **per-resource access-policy enforcement (013)**; **provider-key custody via a pluggable secrets backend + resolve-at-invoke (014)**; **per-identity rate limiting (016, F5)**; **SSRF/egress guard (017, F19 — built unwired)**; **caller-token mint + Redis deny-set + the published invoke plane (018)**. Cross-cutting on every paid/resource path: tenancy/RLS, RBAC role-gate, per-resource access policy, rate limit, spend cap, correlation; the deny-set additionally gates both invoke planes incl. in-flight streams.
- **Backend DEFERRED:** console user-login (auth-code/OIDC) (019) + IdP group→member/team sync (020), tool approvals, Redis spend-reservation + degraded-mode/PgBouncer hardening (021), heavy observability (Langfuse/ClickHouse) — M5/M6-remainder; channels + workspace-root — P2. *(Token mint + deny-set is now BUILT (018); provider-key custody BUILT (014); the SSRF guard is BUILT but unwired (017) — no tool-execution path exists until P2. MCP/tool/data-source/channel credential storage remains designed-but-not-built behind the same secrets seam.)*
- **Frontend seams wired to real API:** **0 / 257** (pending Node toolchain for client-gen + hook swap).

## How to complete the frontend wiring (in a Node environment)

```bash
cd frontend
npm install                 # installs openapi-typescript (now in devDependencies)
npm run gen:api             # frontend/openapi.json → src/lib/api/schema.ts (typed client)
# then swap representative hooks in src/lib/store.ts (useAgents/addAgent/updateAgent, playground send,
#   eval-run, publish, useTools/addTool/updateTool, skills) mock→real, preserving hook shapes
npm run lint && npm run build   # zero type errors / zero client drift (SC-003)
```

Update this tracker's **FE wired** column to REAL as each family's hooks are swapped and build-verified.

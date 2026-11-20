# Implementation Plan: Per-Resource Access-Policy Enforcement

**Branch**: `feat/backend-m0-foundation` | **Date**: 2026-11-09 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `specs/013-access-policy-enforcement/spec.md`

## Summary

Enforce the per-resource access-policy contract (already authoritative in `frontend/src/lib/access-policy.ts`)
on the backend as a deterministic, fail-closed control layered **after** RLS tenancy and **after** the route's
RBAC role-gate. A single pure evaluator (mirroring `access-policy.ts`) decides USE/MANAGE for a principal against
a resource's `access_policy`; a thin dependency resolves the caller's role + teams + subject; each in-scope
router applies the evaluator at list / read / manage / invoke boundaries with **no existence leak** (invisible →
404; visible-but-unmanageable → 403). Member teams and the `access_policy` column are added by one migration; the
console `Member` contract + members UI gain `teams` (mock/type level, build-verified). `runtime/` stays pure —
all enforcement is in the API layer.

## Technical Context

**Language/Version**: Python 3.12 (services), TypeScript/React 19 + Next.js 16.3.8 (frontend contract-sync only)

**Primary Dependencies**: FastAPI/Starlette, SQLAlchemy 2.0 async (asyncpg), Alembic, Pydantic v2 — all already
present. **No new backend dependency.** Frontend: existing toolchain (`openapi-typescript`, Tailwind).

**Storage**: PostgreSQL (pgvector/pg16). New: `members.teams text[]`; `access_policy jsonb` on `agents`, `tools`,
`mcp_servers`, `mcp_tools`, `skills` (knowledge_bases already has it). All default to the ungoverned value.

**Testing**: pytest + pytest-asyncio (`asyncio_mode=auto`), httpx ASGITransport client, RLS tenant session —
the existing integration harness. Frontend: `npm run lint && npm run build` (no FE unit runner exists).

**Target Platform**: Linux server (containerized per the deployment slice).

**Project Type**: Web service (FastAPI backend) + a mock Next.js console (contract-sync only).

**Performance Goals**: Policy evaluation is O(policy size) pure Python in-process — negligible. List filtering is
post-RLS in-process over a page; no extra round-trips beyond one teams read per request (cached on the principal).

**Constraints**: Fail-closed on every ambiguity; zero existence leak (P5); `runtime/` imports no HTTP/web
framework (P6); no secret/token in logs (P5/P8). Backend becomes the authoritative evaluator; FE preview must
produce identical decisions (FR-013).

**Scale/Scope**: Internal pilot, one deployment. In scope: 6 resource families × {list, read, manage, use}.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | How this plan satisfies it |
|---|---|---|
| **P1/P9 Honesty** | No mock-as-real; completion names commands + unverified items | FE stays 0/253 wired (contract-sync only, explicitly documented); completion report will name the pytest commands and any unverified requirement. |
| **P2 Scope/phases** | Belongs to an approved phase; no silent scope change | M5 remainder (access policy was always M5). No new scope decision needed; no `docs/scope.md` reversal. Non-goals explicit (token issuance, IdP team sync, Vault, OIDC login). |
| **P3 Deterministic, fail-closed** | Controls in code; least privilege; fail-closed | Pure evaluator in ordinary code; every ambiguous/empty/malformed policy denies non-admin/non-owner; invoke denied before any side effect. |
| **P4 Evidence/judge** | — | N/A — no model/judge/eval gate touched. Recorded as not-applicable. |
| **P5 Tenancy + no leak** | Server-side authz + RLS defense-in-depth; **negative cross-tenant/cross-principal tests**; no existence leak; no secret leak | Enforcement composes *after* RLS; negative cross-principal tests are the headline acceptance set; invisible resources return 404 not 403; decision logs carry no secret/token. |
| **P6 Runtime purity** | `runtime/` imports no HTTP/web framework | Evaluator + enforcement live in `api/`; invoke enforcement is in `api/routers/invoke.py` *before* calling `runtime.invoke`. Purity test stays green. |
| **P7 Provider/cost honesty** | — | N/A — no provider/cost path changed. Recorded as not-applicable. |
| **P8 Observability** | Decisions observable; no secret leak; degradation explicit | Deny decisions logged (tier, capability, resource id, subject — no secret); existing `ac_requests_refused_total{code}` counts 403s. |
| **P9 TDD** | Red→Green→Refactor; named commands; owner sees Red | Every behavior task is Red-first against the integration harness; focused commands named in tasks.md; Red evidence shown before Green. |

**Minimum Verification Checks that apply:** Database/tenant (migration + isolation + **negative cross-principal**);
API/integration (typed contracts + FE type/build); Security/secrets (authz/abuse cases + no-leak evidence). Not
applicable (recorded): Provider/runtime cost, Model/prompt eval, Paid provider calls — this feature makes none.

**Result: PASS.** No violations; Complexity Tracking not required.

## Project Structure

### Documentation (this feature)

```text
specs/013-access-policy-enforcement/
├── plan.md              # this file
├── research.md          # Phase 0 — decisions (evaluator placement, 404-vs-403, teams source, parity)
├── data-model.md        # Phase 1 — access_policy shape, member.teams, migration 0006
├── quickstart.md        # Phase 1 — how to run the negative-cross-principal suite + FE build
├── contracts/
│   ├── access-policy-evaluation.md   # the pure decision table (use/manage × tier × plane) — mirrors access-policy.ts
│   └── enforcement-http.md           # per-boundary HTTP behavior (list/read/manage/invoke; 404 vs 403)
├── checklists/
│   └── requirements.md  # spec-quality checklist (passing)
└── tasks.md             # Phase 2 — /speckit-tasks (NOT created here)
```

### Source Code (repository root)

```text
services/
├── api/
│   ├── access_policy.py            # NEW — pure evaluator: can_use(policy, p), can_manage(policy, p); PolicyPrincipal; parse/normalize. Mirrors access-policy.ts. No DB/HTTP imports.
│   ├── deps.py                     # EDIT — add access_principal dependency (role+teams+subject for ws); teams read; enforcement helpers (ensure_use/ensure_manage raising NotFound/AccessDenied)
│   ├── membership.py               # EDIT — teams_for(session, subject) (or extend role_for → role+teams) under RLS
│   ├── models.py                   # EDIT — Member.teams (text[]); access_policy on Agent/Tool/McpServer/McpTool/Skill
│   ├── schemas/
│   │   ├── members.py              # EDIT — MemberOut/MemberIn gain teams
│   │   ├── agents.py tools.py mcp.py skills.py knowledge.py  # EDIT — create/patch accept access_policy; Out surfaces it
│   └── routers/
│       ├── agents.py tools.py mcp.py skills.py knowledge.py members.py invoke.py  # EDIT — apply evaluator at list/read/manage/invoke; owner-on-create
├── migrations/versions/
│   └── 0006_access_policy_and_member_teams.py   # NEW — add columns (backfill-safe defaults)
└── tests/
    ├── test_access_policy_eval.py              # NEW (unit) — decision table vs access-policy.ts (incl. executer plane)
    ├── test_api_access_policy.py               # NEW (integration) — negative cross-principal across families; 404-no-leak; admin bypass; owner
    └── test_api_members_teams.py               # NEW (integration) — member teams round-trip + RLS

frontend/
├── src/lib/data.ts                # EDIT — Member type + create/update payloads gain teams (@backend)
├── src/lib/iam.ts / auth.tsx      # EDIT (docs/seams) — teams server-owned for authZ; MOCK_PROFILE preview note
├── src/app/(app)/settings/…       # EDIT — members UI team assignment (READ node_modules/next/dist/docs first)
└── openapi.json                   # REGEN — MemberOut/MemberIn include teams
```

**Structure Decision**: Single backend service (`services/api`) + the existing mock console. A **new pure module
`api/access_policy.py`** owns evaluation (unit-testable, mirrors the TS); routers call shared **enforcement
helpers** in `deps.py`. This keeps evaluation in one place (one decision table to keep parity with the FE) and
enforcement uniform across the 6 families. No change under `runtime/` (P6 preserved).

## Phase 0 — Research (decisions)

See [research.md](./research.md). Key decisions resolved there:

1. **Evaluator placement** → `api/access_policy.py` (API-layer pure module), not `runtime/` — invoke enforcement
   wraps the call to `runtime.invoke` at the router, preserving P6 purity.
2. **404-vs-403 rule** → anchored on USE visibility: cannot-USE ⇒ 404 everywhere (list omit, read, manage,
   invoke); can-USE-but-not-MANAGE ⇒ 403 on manage. RBAC 403 still fires first (role-coarse, non-leaking).
3. **Teams source** → `members.teams text[]`, resolved per-request under RLS and cached on the principal, exactly
   as role is (feature 012). One read per request.
4. **Owner model** → owner lives inside `access_policy.owner` (matches `access-policy.ts`); set to the creating
   subject when a resource is created/edited to `private` without an explicit owner (FR-012).
5. **Parity strategy** → a canonical JSON decision-table fixture derived from `access-policy.ts`; the Python unit
   test asserts the evaluator against it; the fixture is the contract both sides honor (FR-013/SC-003).
6. **Migration safety** → additive columns with ungoverned defaults; existing rows become `workspace` (no
   behavior change) — no data backfill needed.

## Phase 1 — Design & Contracts

- [data-model.md](./data-model.md): the `AccessPolicy` JSON shape + validation/normalization rules; `Member.teams`;
  migration `0006` column list and defaults; which Out/In schemas change.
- [contracts/access-policy-evaluation.md](./contracts/access-policy-evaluation.md): the full decision table —
  `(visibility × capability × caller-plane × policy-shape) → allow/deny` — mirroring `access-policy.ts` line for
  line, including the executer team-only rule, empty-restriction fail-closed, private-owner, admin bypass.
- [contracts/enforcement-http.md](./contracts/enforcement-http.md): per-endpoint behavior and status codes
  (list filter; GET 404; PATCH/publish/archive 404-then-403; invoke 404; create owner-on-private), and the
  ordering RLS → RBAC → access policy.
- [quickstart.md](./quickstart.md): the named commands to reproduce the negative-cross-principal suite, the unit
  decision-table test, the purity check, and the FE build.

**Post-design Constitution re-check: PASS** — design introduces no HTTP/DB import into `runtime/`, adds no
provider/cost path, and makes the fail-closed + no-leak behavior the central tested contract.

## Complexity Tracking

> No constitution violations — section intentionally empty.

# Research: Per-Resource Access-Policy Enforcement

All unknowns from Technical Context resolved below. Each item: Decision / Rationale / Alternatives considered.

## R1 — Where does the evaluator live? (P6 purity)

- **Decision**: A new pure module `services/api/access_policy.py` holds the evaluation functions
  (`can_use`, `can_manage`) and a small `PolicyPrincipal`. It imports nothing from FastAPI/HTTP/DB. Invoke-path
  enforcement happens in `api/routers/invoke.py` *before* calling `runtime.invoke(...)`.
- **Rationale**: Access policy is an **authorization** concern at the API edge, not a runtime-contract concern.
  Keeping it in `api/` preserves the constitutional invariant that `runtime/` imports no web framework (P6) and
  that playground/eval/invoke all share the *same* `runtime.invoke` — enforcement wraps the caller, it does not
  fork runtime behavior. The evaluator being pure keeps it unit-testable against the FE decision table.
- **Alternatives**: (a) Enforce inside `runtime.invoke` — rejected: injects authZ + (eventually) DB lookups into
  the pure library, breaking P6 and the single-runtime invariant. (b) Inline the checks in each router — rejected:
  six copies of subtle fail-closed logic drift apart; one evaluator = one place to keep FE parity.

## R2 — 404 vs 403 (P5 no existence leak)

- **Decision**: Anchor the choice on **USE visibility**. If a principal cannot USE a resource, every denial about
  it is a 404 (`NotFound`) — list omission, GET, and management attempts — indistinguishable from the resource
  not existing. If a principal can USE (see) a resource but cannot MANAGE it, management is denied with 403
  (`AccessDenied`) because existence is already known to them. The route-level RBAC gate (`require_permission`)
  still raises 403 first — it is role-coarse and resource-independent, so it leaks nothing about a specific row.
- **Rationale**: Directly satisfies FR-008/FR-009 and the constitution's "no existence leak" (P5). Matches the
  existing API convention (RBAC 403-before-404) while adding the finer resource-level layer underneath it.
- **Alternatives**: Always-403 — rejected (confirms existence of hidden resources). Always-404 even for
  visible-but-unmanageable — rejected (hides a legitimately-visible resource's existence from someone who can see
  it, producing confusing UX and contradicting the FE which shows the resource but disables manage).

## R3 — Team membership source

- **Decision**: Add `members.teams text[]` (default `'{}'`). Resolve the caller's teams per request under the RLS
  tenant session (same query path as `console_role`), cache them on the request principal so each request makes
  at most one teams read. A dedicated `membership.teams_for(session, subject)` (or an extended `role_for`) returns
  them.
- **Rationale**: Feature 012 already made the `members` store the authoritative source for the caller's role under
  RLS (the real token has no role/team claims). Teams belong in the same place for the same reason; a column is the
  simplest shape and the per-workspace member row already scopes teams to the workspace (RLS). IdP group→member
  team sync is a separate, deferred feature (OIDC login).
- **Alternatives**: (a) `member_teams` association table — rejected for now: heavier migration + a join on every
  evaluation, no current need for team metadata or many-to-many beyond a string list. (b) Teams from the token
  `groups` claim — rejected: the real Trellix-IAM token carries only `xdr.*` scopes, no groups; and 012 already
  moved authZ off the token into the store.

## R4 — Owner model for `private`

- **Decision**: Store the owner **inside** `access_policy.owner` (a subject string), exactly as
  `access-policy.ts` models it — no separate owner column. On create (or edit) to `private` with no explicit
  owner, the router sets `owner = principal.subject` (FR-012).
- **Rationale**: Keeps one policy object as the single source; matches the FE shape so parity holds; avoids a
  column per family. Admin bypass means an ownerless/blank-owner private resource is still repairable.
- **Alternatives**: A dedicated `owner_subject` column per family — rejected: redundant with the policy JSON and
  diverges from the FE contract.

## R5 — FE ↔ BE decision parity (FR-013 / SC-003)

- **Decision**: Encode the canonical decision table as a JSON fixture
  (`contracts/` + a test fixture) covering every combination of {visibility × capability × caller-plane ×
  policy-shape (role-only / team-only / both / empty) × admin / owner / other}. The Python unit test asserts the
  evaluator matches the fixture. The fixture is derived verbatim from `access-policy.ts` semantics and is the
  shared contract both sides honor.
- **Rationale**: The FE has no unit-test runner today (project CLAUDE.md), so a cross-language runtime assertion
  isn't wired. A shared fixture + a Python assertion gives a concrete, reviewable parity artifact now; a future FE
  test can assert `access-policy.ts` against the same fixture with zero rework.
- **Alternatives**: Port the table into a node script run in CI — deferred (nice-to-have; the fixture already
  pins the contract). Trust-by-reading — rejected (not verifiable).

## R6 — Migration safety

- **Decision**: One additive migration `0006_access_policy_and_member_teams` adds `members.teams` (default `'{}'`,
  NOT NULL) and `access_policy jsonb` (default `'{"visibility":"workspace"}'`, NOT NULL) to `agents`, `tools`,
  `mcp_servers`, `mcp_tools`, `skills`. Existing rows take the ungoverned default → zero behavior change until an
  owner sets a policy. A downgrade drops the columns.
- **Rationale**: Backfill-free, reversible, and preserves current behavior (FR-001). Migration test asserts
  upgrade head applies and the defaults are present.
- **Alternatives**: Per-family separate migrations — rejected (one logical change = one migration). A nullable
  column with app-side default — rejected (DB default makes the ungoverned behavior authoritative and simpler).

## R7 — Which paths enforce, and interaction with existing gates

- **Decision**: Enforce at: every family's `GET list` (filter), `GET /{id}` (404-if-not-use), `PATCH`/`publish`/
  `archive`/`restore` (404-if-not-use then 403-if-not-manage), and the agent `invoke` path (404-if-not-use, before
  any provider call). Ordering per request: **RLS (tenant_db) → RBAC (require_permission) → access policy**. Admin
  bypasses the access-policy layer only.
- **Rationale**: Covers read/use/manage comprehensively and fail-closed on the paid path (FR-010). The ordering
  keeps tenancy and role checks first (cheap, non-leaking) and the resource-level policy last (needs the loaded
  row).
- **Alternatives**: Enforce only on invoke + read — rejected (manage operations would bypass restricted/private).

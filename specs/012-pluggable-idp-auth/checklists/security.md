# Security Requirements Quality Checklist: Pluggable IdP Bearer-Token Auth

**Purpose**: Pre-implementation requirements-quality review (authN/authZ focus) — "unit tests for the spec".
**Created**: 2026-11-08
**Feature**: [spec.md](../spec.md)
**Depth**: Standard · **Audience**: implementer + reviewer

> Ownership: this is a reviewer-owned review artifact. `[x]` means the reviewer judged the requirements-quality
> criterion satisfied — NOT that implementation is done. `/speckit-implement` reads but does not modify these.
> Items are intentionally left unchecked for the reviewer.

## Token Verification Completeness

- [ ] CHK001 Are all mandatory JWT validation steps (signature, `iss`, `aud`, `exp`, `nbf`, `iat`) each specified as separate requirements? [Completeness, Spec §FR-001/FR-002/FR-003]
- [ ] CHK002 Is the signing-algorithm allow-list requirement specified, including explicit rejection of `alg=none` and unexpected algorithm families? [Completeness, Spec §FR-004]
- [ ] CHK003 Is the required behavior specified for every distinct failure mode (missing, malformed, bad-signature, wrong-aud, wrong-iss, expired, not-yet-valid, disallowed-alg, unknown-kid, JWKS-unavailable, unconfigured-provider)? [Coverage, Spec §FR-010/Edge Cases]
- [ ] CHK004 Is it specified that verification happens BEFORE workspace/resource resolution and permission checks (ordering requirement)? [Completeness, Spec §US1/FR-001]

## Clarity & Measurability

- [ ] CHK005 Is "bounded clock skew" quantified (a concrete default + an upper bound), rather than left as an adjective? [Clarity, Spec §FR-003/data-model]
- [ ] CHK006 Is the JWKS cache behavior specified with a measurable policy (TTL + "refresh at most once on unknown `kid`, then reject")? [Clarity, Spec §FR-017]
- [ ] CHK007 Are the exact minimum fields the normalized Principal MUST carry (subject, scopes, issuer) enumerated unambiguously? [Clarity, Spec §FR-013/Key Entities]
- [ ] CHK008 Is "fail closed" defined concretely (reject + no partial/default Principal + no downstream resolution) rather than used as a slogan? [Measurability, Spec §FR-010]
- [ ] CHK009 Are the success criteria for the 401 matrix stated so they can be objectively verified (each failure class → 401, no leak)? [Measurability, Spec §SC-001]

## AuthN / AuthZ Boundary Consistency

- [ ] CHK010 Is the separation between authentication (this feature) and authorization (RBAC 008 + membership) stated consistently across FR-012, FR-013, and the Assumptions? [Consistency, Spec §FR-012/FR-013]
- [ ] CHK011 Is it unambiguous that IdP scopes (`xdr.*`) are carried but NOT mapped to AC permissions in this slice? [Clarity, Spec §FR-013/Clarifications Q1]
- [ ] CHK012 Are the requirements consistent that a verified token is necessary but NOT sufficient for access (membership still required)? [Consistency, Spec §FR-012/FR-014]
- [ ] CHK013 Does the spec define the role source unambiguously (membership store `console_role`) and reconcile it with the existing RBAC role vocabulary? [Consistency, Spec §FR-013/data-model]

## Tenancy & No-Leak (P5)

- [ ] CHK014 Is the target-workspace resolution rule specified so a caller cannot act on a workspace it has no membership in? [Completeness, Spec §FR-014]
- [ ] CHK015 Is the no-existence-leak requirement explicit for the non-member case (403/404 without revealing resource existence)? [Coverage, Spec §FR-014/SC-001]
- [ ] CHK016 Is the membership lookup specified to stay within tenant (RLS) scope, i.e. no cross-tenant read? [Consistency, Spec §research R3/FR-016]
- [ ] CHK017 Is a cross-tenant negative scenario (membership in ws-A, request for ws-B → denied) stated as a required behavior? [Coverage, Spec §contracts §5-16]

## Secret & Data Protection (FR-011 / FR-021)

- [ ] CHK018 Is the prohibition on logging/tracing/echoing/persisting the raw bearer token stated as a requirement? [Completeness, Spec §FR-011]
- [ ] CHK019 Is it specified that provider secrets (client secret, private keys) never appear in output, config errors, or diagnostics? [Completeness, Spec §FR-011/R5]
- [ ] CHK020 Is the allowed diagnostic surface (non-sensitive reason, issuer, `kid`) explicitly bounded so it cannot include token contents? [Clarity, Spec §FR-011]

## Pluggability & Configuration Safety

- [ ] CHK021 Is the vendor-agnostic contract (`verify → Principal`) specified precisely enough that a new provider needs no call-site change? [Completeness, Spec §FR-005/SC-003]
- [ ] CHK022 Is provider selection + required per-provider config specified as config-driven (not hard-coded)? [Completeness, Spec §FR-006]
- [ ] CHK023 Is the production-profile gate specified so the unverified dev provider is unusable in production (fail-closed)? [Coverage, Spec §FR-008/Edge Cases]
- [ ] CHK024 Is the behavior specified when config names an unknown or misconfigured provider (startup/first-request fail-closed, no secret in message)? [Edge Case, Spec §US2/R5]
- [ ] CHK025 Is the exact count of providers built this slice (trellix-iam + dev only; none stubbed) stated to bound scope? [Clarity, Spec §FR-018/Clarifications Q4]

## Testing-as-Requirement (FR-015)

- [ ] CHK026 Is the requirement explicit that automated tests use local keys only — no live IdP calls and no real secret in the suite? [Completeness, Spec §FR-015/SC-004]
- [ ] CHK027 Is the live-IdP validation explicitly scoped as manual/opt-in and excluded from CI, with retained evidence? [Coverage, Spec §US3/SC-007]
- [ ] CHK028 Is the purity requirement (no auth/jwt/http imports in `runtime/`/`evals/`) stated as a verifiable criterion? [Measurability, Spec §FR-016/SC-005]

## Dependencies & Assumptions

- [ ] CHK029 Are the Trellix-IAM assumptions (issuer, audience=`mcafee`, jwks_uri, RS256) documented and traceable to verified evidence rather than guessed? [Assumption, Spec §Assumptions/research R2]
- [ ] CHK030 Is the "no new migration / reuse existing `members`" assumption stated and consistent with the authZ decision? [Assumption, Spec §Assumptions/FR-013]
- [ ] CHK031 Is the backward-compatibility affordance (dev-header claim vs membership read in `resolve_workspace`) documented as a deliberate, reviewed choice rather than an implicit fork? [Assumption, Spec §research R4]

## Edge Cases & Exception Flows

- [ ] CHK032 Is behavior specified when both a bearer token and a dev header are present (precedence rule)? [Edge Case, Spec §Edge Cases]
- [ ] CHK033 Is behavior specified when the JWKS source is unreachable (fail-closed, never accept unverified)? [Exception Flow, Spec §Edge Cases/FR-017]
- [ ] CHK034 Is behavior specified for a verified token whose subject has no membership anywhere / in the asserted ws (distinct from an unverified token)? [Edge Case, Spec §Edge Cases/FR-014]
- [ ] CHK035 Are requirements defined for a token missing the `scope` claim entirely (empty scopes vs reject)? [Gap, Spec §FR-007]

## Notes

- Scope of this review: **security/authN-authZ requirements quality only**. Console-login (auth-code) flow,
  token mint/deny-set, and Vault custody are out of scope (separate slices) and need not be covered here.
- Potential gap to watch during review: **CHK035** — the spec does not explicitly state the required behavior
  when the `scope` claim is absent/empty; confirm whether that is intended (empty scopes allowed, authZ still
  from membership) or should be an explicit requirement.

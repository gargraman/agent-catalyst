# Review Fixes — the five fix-before-building items

> Companion to `docs/archive/architecture-review-v2.md` (§5 register, §10 fix-options, §12 roadmap). This is the
> durable, actionable cut: the five findings that must be resolved *before* building the affected module,
> each with evidence, the decisions still open, concrete fix steps with options, and acceptance criteria.
> **Status date:** 2026-10-22. **Nothing here is built yet** — these are design/DDL/spec fixes.
>
> Scope note: "the five" = **C1, C2, C3, C4, H1** (the fix-before-building set). C5 (build-ahead-of-MVP) is a
> sequencing decision, not a code fix, so it lives in the review, not here.

> **✅ ALL FIVE RESOLVED at the design-doc level (2026-10-22).** C1 (claim-based + IdP-agnostic auth, doc 18),
> C2 (atomic publish gate + hash property test), C3 (KB content live & independent; **one PARKED sub-decision** —
> KB-refresh eval signal), C4 (per-user DEK ratified + org-plane DB boundary + worker GUC), H1 (verified-email +
> domain bind + idempotency). These are design/DDL/spec fixes; **code is still unbuilt**.

| ID | One-line | Class | Decisions open? | Status |
|---|---|---|---|---|
| C1 | Auth/tenancy reversal not propagated into DDL + guard | confirmed-defect · Critical | Resolved (claim-based + adapter) | ✅ applied |
| C2 | Eval-gate integrity rests on unenforced hashing + TOCTOU | probable-defect · Critical | No | ✅ applied |
| C3 | RAG pinning cosmetic — re-ingest changes live retrieval | confirmed-defect · High | **1 PARKED (refresh eval signal)** | ✅ applied (minus parked) |
| C4 | Tenant isolation single-points unguarded | architectural-risk · Critical | Resolved (per-user DEK) | ✅ applied |
| H1 | Channel federation trusts platform-asserted email | architectural-risk · High | No | ✅ applied |

Dependency order to build safely: **C1 → C4 → (C3, C2) → H1**. C1 and C4 block safe multi-tenant build; do
them first. All are independently shippable and expand/contract-compatible (nothing is live).

---

## C1 · Auth/tenancy reversal — propagate the group-driven model (or decide to change it)

> ### ✅ RESOLVED (2026-10-22) — **SUPERSEDED by a further pivot, not the group-driven fix below.**
> The group-driven model this section proposed propagating was itself replaced by the **claim-based,
> IdP-agnostic** model (scope.md rev1/rev2; docs `09/01/08/12/15/16` + new `18`): per-workspace IdP role claims
> mapped by `idp_binding`, a `viewer`/`builder`/`publisher` ladder, an app-wide `admin` claim (no `platform_admins`
> table), an `executer` caller role, and an `IdentityProvider` adapter boundary. The DDL↔guard↔prose
> disagreement is closed on the **docs** side. **The three "OPEN DECISIONS" below were all answered:** no viewer →
> *reversed, viewer kept*; mutating admin + workspace CRUD → *accepted*; one-group-per-workspace → *replaced by
> per-workspace claims*. **The group-driven body below is retained for history only — do not build from it.**
> **FE is fully migrated** to the claim model (scope.md rev 5–6; `09 §1`), **including** the `executer` caller
> role (`runtime-access.ts` + `access-policy.ts` `CallerRole`; `npm run build` green). C1 is closed end-to-end.

### Problem
The 2026-10-20 "Okta-group-driven" pivot is announced in prose but **not applied to the two normative build
artifacts** (the DDL and the tenancy guard), while the **frontend already implements group-driven**. An
Alembic migration generated from `01` today would build the model the design says it deleted. Three systems
(FE, DDL, prose) disagree.

### Evidence
- Announced: `scope.md` decision log 2026-10-20; `09 §2.1` ("REVERSED… superseded… retained for history");
  `01 §3` comment lines 81–91 ("`workspace_access` … REMOVED", "`memberships` … no longer SoR",
  "`workspace_invites` … REMOVED").
- **Not applied (still creates the removed model):** `01 §4.1` line 119 (`access_mode workspace_access`
  column), lines 142–153 (`memberships` table, prose line 135 "the system of record"), lines 157–168
  (`workspace_invites` table); `09 §3 get_workspace_ctx` still calls `principal.role_in(ws_id)` +
  `join_workspace(... access_mode=="open")`; `09 §2` `Principal.memberships`/`role_in`; `09 §4` RBAC table
  documents a working per-workspace `admin`.
- **FE is group-driven (source of truth today):** `frontend/src/lib/iam.ts` — `Workspace.{oktaGroup,
  memberRole}`, `PLATFORM_ADMIN_GROUP` (line 41), `roleFor` (line 84 `return workspace.memberRole`; admin only
  via `isPlatformAdmin`), no roster/invites/self-join.

### Two heavy consequences of group-driven as implemented (must be consciously accepted or rejected)
1. **One role per entire workspace.** Every member of a space shares one `memberRole` (`iam.ts:84`). No
   mixing viewers/builders/publishers in one space; to differentiate you need separate spaces or
   group-per-role (explicitly rejected). Fine for homogeneous spaces; wrong for mixed-seniority teams.
2. **No workspace-local admin → privilege conflation.** `admin` is platform-admin-only, and platform-admin
   *also* reads every tenant's estate (`15`, `09 §1`). So "govern my own space" (eval policy, baseline
   re-freeze, provider keys, per-resource access policy, limits, archive) **cannot be granted without
   org-wide read.** This makes `09 §4`'s RBAC `admin` column fiction and centralizes all workspace
   governance into central ops.

### OPEN DECISIONS (unresolved — needed before the fix)
1. **Is one-role-per-workspace acceptable**, or do real spaces need mixed per-user roles?
2. **Should a space have a local governor** (owns eval policy / baseline / keys / access policies) that is
   **not** a deployment-wide platform-admin?
3. **Confirm group-driven is final** and the FE is the source of truth (update `09`/`01` to match), and
   reconcile `platform_admins` roster-vs-group.

### Fix options
- **Option A — Full group-driven, homogeneous spaces (match FE as-is).** Accept consequences 1+2; governance
  is platform-admin-only. *Trade-off:* coarse roles; central-ops bottleneck; privilege conflation remains.
- **Option B — Group base role + thin app overlay (recommended).** Group confers default role + membership; a
  small app-managed overlay grants specific users a higher role and a **workspace-local admin** bit without
  making them platform-admins. *Trade-off:* reintroduces a *small* roster for overrides only (not base
  membership), keeping IdP as the membership source.
- **Option D — Decouple workspace-admin from platform-admin (do regardless of A/B).** A distinct "space
  governor" grant (own group `ap-<ws>-admin`, or an overlay bit) that governs one space without org-wide read.
- **Option C — group-per-role:** rejected by owner (proliferation). Kept rejected.

### Mechanical fix steps (apply once the model is decided — assumes group-driven)
1. `01 §3`: drop `create type workspace_access`, `invite_status`; keep `member_role` but remove `admin` from it
   if admin is platform-only (or keep + document the overlay if Option B/D).
2. `01 §4.1`: remove the `access_mode` column from `workspaces`; **delete** the `memberships` and
   `workspace_invites` tables (or, Option B, replace `memberships` with a slim `role_overrides` table:
   `(workspace_id, subject_id, role, is_space_admin, granted_by)`); make `okta_group` **not null** +
   authoritative; keep `member_role` on `workspaces`.
3. `09 §2`: replace `Principal.memberships`/`role_in` with group→role resolution
   (`role_for(workspace, groups)`), mirroring `iam.ts roleFor`.
4. `09 §3 get_workspace_ctx`: resolve role from `workspace.okta_group ∩ principal.groups` (+ overlay if B);
   remove `join_workspace`/`access_mode=="open"` self-join path; keep the RLS `SET LOCAL` pin.
5. `09 §4`: rewrite the RBAC table to the real model — `{viewer,builder,publisher}` per-workspace + either
   platform-admin-only governance (A) or a space-governor tier (B/D).
6. `platform_admins`: pick roster (`01 §4.9`/`15`) **or** group (`iam.ts`). If roster-with-optional-group-
   upsert, state that the group is non-authoritative and the FE must stop treating `PLATFORM_ADMIN_GROUP` as
   the source of truth.
7. Delete the "retained for history" paragraphs in `09 §2.1` (history is in git).

### Acceptance criteria
- A migration generated from `01` builds **no** `memberships`/`workspace_invites`/`access_mode` (or only the
  slim overlay in Option B).
- `09 §3` guard resolves role purely from group (+ overlay) and matches `iam.ts roleFor`.
- `09 §4` RBAC table describes capabilities a real role actually has.
- The three open decisions are recorded in `scope.md` decision log with their rationale.

---

## C2 · Eval-gate integrity — enforce the hashing invariants + close the publish-read race

### Problem
The eval gate is the product's #1 guarantee, but its correctness now depends on (a) `snapshot_hash` folding
in everything behavior-changing, stated only as prose "MUST"s, and (b) a non-atomic publish-time read of
"latest eval vs latest trigger."

### Evidence
- Hash coverage is prose, not enforced: `05 §6` ("`snapshot_hash` MUST incorporate … `retrieval_config` …
  `kb_index_version` … skill content hash"), `08 §3` VersionCreate note. If any is omitted, the verdict
  cache (keyed on `candidate_hash = snapshot_hash`) serves a **stale PASS** and the gate is bypassed silently.
- TOCTOU: `05 §5 assert_publishable` reads `latest_eval_run(version)` and `latest_trigger_time(version)`
  separately. A trigger firing between the reads, or a concurrent publish, is a race (it fails *closed* in the
  common case, but the window is unspecified).

### ✅ RESOLVED (2026-10-22) — applied to `05 §5`: advisory-lock'd atomic publish gate + the hash-coverage property-test spec (excludes `kb_index_version`, parked per C3).
### Fix steps
1. **Property test over the full trigger set** (`DRIFT_TRIGGERS`, `05 §5`): mutating any of
   model/provider/api_version/params/prompt_hash/guardrail_config/tool_bindings/skill_bindings/
   skill_content_hash/embedding_model/kb_binding/retrieval_config/kb_index_version/capability_substitution
   **must** change `snapshot_hash`. One red test per trigger; run in CI.
2. **Make the publish read atomic:** either one query that joins "latest run" against "latest trigger time",
   or take a per-version advisory lock (`pg_advisory_xact_lock`) for the duration of the gate check + publish
   write, so no trigger can interleave.
3. Keep `allow_cache=False` on eval candidate runs (already specified, `05 §7`) — do not regress it.

### Acceptance criteria
- CI: the trigger-set property test is green and fails if a new frozen field is added without folding into
  `snapshot_hash`.
- A concurrent "edit (trigger) + publish" test cannot serve a stale PASS.

---

## C3 · RAG pinning is cosmetic — make a re-ingest not change live published retrieval

### Problem
`04 §9` claims a published version "still runs on its pinned `index_version` until an eval-gated re-point,"
but retrieval never filters by `index_version` and ingest mutates chunks in place — so a re-ingest of a bound
KB **immediately changes what the already-published live endpoint retrieves**, breaking the core "editing
changes nothing live" invariant for the RAG path. *(Verified line-by-line.)*

### Evidence
- Claim: `04 §9` line 195.
- Retrieval ignores it: `04 §6` SQL (lines 131–137) filters only `workspace_id` + `kb_id`; `pgvector_knn`
  call (lines 119–122) passes no `index_version`.
- In-place mutation: `04 §2` ingest (lines 37–42) `upsert_document` + `write_chunks` then
  `bump_index_version`; single `chunks.embedding vector(N)` column can't hold two versions; a mid-loop
  failure leaves the KB half-updated with no rollback.

### RESOLVED (2026-10-22): KB content is LIVE and INDEPENDENT of the agent version
The premise "a re-ingest must not change live retrieval" is **rejected by design**. Decided model: the agent
version pins the **binding** (which KB + `retrieval_config` + `embedding_model`), **not** the KB's content. A
KB refresh (periodic or manual) updates chunks in place and does **not** bump the agent version — the two
events are independent (04 §9). So:
- **Options A & B (chunk/index versioning, copy-on-write) are DROPPED** — no `chunks.index_version` pinning,
  no N-version storage, no GC. The false `04 §9` "runs on pinned index_version until eval-gated re-point" claim
  is removed.
- There is **no freeze** (Option C's freeze is also dropped) — re-ingest of a bound KB is allowed and goes
  live immediately, by design.

### ⚠️ PARKED (open decision — revisit later)
Whether a KB **content refresh** carries an **eval/drift signal** — flag bound published agents `needsReEval`
+ notify on regression (the FE already models `needsReEval` on data-source change), vs. fully fire-and-forget —
and therefore whether `kb_index_version` folds into `snapshot_hash` / busts the verdict cache (05). **Deferred
by decision.** Consequence to accept meanwhile: a content refresh can change a live agent's answers with no
drift signal. The `05` RAG snapshot-hash note is marked provisional until this is resolved.

### Also fix regardless of option
- **Ingest atomicity/rollback** (`04 §2`): wrap a re-ingest so a mid-loop failure does not leave new chunks +
  old version marker; either transactional or write-new-version-then-flip.
- **Dedup must consult the embedding model** (M7): `04 §2` line 35 `unchanged(content_hash)` skips re-embed
  when only the embedding model changed → silent no-op "re-ingest." Key dedup on
  `(content_hash, embedding_model, chunk_config)`.

### Acceptance criteria
- The false "runs on a pinned `index_version` until an eval-gated re-point" claim is removed (`04 §9`); docs
  state KB content is live & independent of the agent version.
- `snapshot_hash` covers the **binding** (which KB + `retrieval_config` + `embedding_model`); it does **not**
  cover KB content (`kb_index_version`) — pending the parked eval-signal decision.
- A failed ingest rolls back cleanly (no half-updated KB). *(Robustness — do regardless.)*
- Changing a KB's embedding model actually re-embeds (dedup keyed on `(content_hash, embedding_model,
  chunk_config)`, no silent short-circuit). *(Robustness — do regardless.)*
- **Parked:** eval/drift signal on content refresh (`needsReEval` + notify, vs. none) — revisit.

---

## C4 · Tenant isolation — eliminate the single-point assumptions

### ✅ RESOLVED (2026-10-22) — per-user-MCP DEK ratified **per-user** (09 §9/§9.1 now match 17 §3); org-plane runs on a separate `platform_report` connection with app role lacking `EXECUTE` + `enrich_estate` via definer fns (15 §2); worker-GUC + org-plane-boundary + per-user-DEK cross-tenant/cross-user cases added to the 09 §8 isolation suite. RLS transaction-scoped pool + no-`BYPASSRLS` were already in 09 §7.

### Problem
Several isolation controls are each one mistake from a cross-tenant leak, and one (per-user-MCP DEK scope) is
a direct contradiction between two docs.

### Evidence + fix per point
1. **RLS pool bleed** (`09 §7`, `01 §6`): RLS only holds if `SET LOCAL app.workspace_id` can't leak across
   pooled checkouts. *Fix:* enforce transaction-scoped pool mode (PgBouncer `transaction`, or per-request
   SQLAlchemy txn) in code; CI assertion the app role has no `BYPASSRLS`.
2. **SECURITY-DEFINER org plane role boundary** (`15 §2`, gov-review F1): the function is RLS-free; the only
   DB control is who holds `EXECUTE`. The docs never say the app opens a **separate** DB role/connection for
   the org plane — if the normal app role holds `EXECUTE`, any logic bug/SQLi anywhere reads all tenants.
   *Fix:* dedicate a `platform_report` DB role + a separate connection used only by the platform-admin plane;
   CI assertion the general app role lacks `EXECUTE` on `org_report_*`; add a DB-level (not just app-level)
   isolation test.
3. **Worker GUC scoping** (`00 §2`, `09 §7`): the worker runs jobs for many tenants and must set
   `app.workspace_id` per job. *Fix:* specify + test that every worker job sets (and resets) the GUC; add a
   `tool_test`/sub-agent case to the isolation suite (already named `09 §8`).
4. **DEK scope contradiction** (`09 §9` ↔ `17 §3/§8`): per-user-MCP creds are "per-workspace DEK" in `09`,
   "per-user DEK" in `17`. *Fix:* pick **per-user DEK** for per-user MCP connections (least-privilege, matches
   the per-user audit goal); correct `09 §9`; add an isolation-suite case that tenant A cannot decrypt tenant
   B's per-user-MCP secret.

### Acceptance criteria
- Isolation suite (`09 §8`) passes including: a DB-level test that the app role can't call the org functions;
  a per-user-MCP cross-tenant decrypt test; a worker-job GUC-reset test.
- `enrich_estate` (gov-review F5) is routed through definer functions/matview, not the RLS-bound session
  (otherwise the org report returns empty/errors) — fold this into the org-plane fix.

---

## H1 · Channel federation — stop trusting a platform-asserted email as the Okta join key

### Problem
Signature verification proves a chat event came from Slack/Google intact — **not** that the sender's email is
owned/verified by them. Any external/guest/Slack-Connect/personal account whose email **collides** with an
internal Okta user federates straight into that Okta subject, with full agent access as that user.

### Evidence
- `12 §4`: `email = msg.platform_user.email; sub = okta.lookup_subject(email)` ("email is the join key").
- `channels-design §2.2`: "Requires the platform to expose email … and for it to match the Okta identity";
  `§6/§2` call signature verification "the sole proof of user identity."
- No `email_verified`/domain-bound claim check specified on any federation path; nothing restricts the
  platform workspace to verified corporate-domain accounts.

### ✅ RESOLVED (2026-10-22) — applied to `12 §4`: `email_verified` + corporate-domain bind (reject guest/Slack-Connect collisions), email documented non-authoritative + hardened-tenant precondition, inbound event-id idempotency + dead-letter, and the Teams-OBO side-issue folded into the IdP adapter (`exchange_obo`, `teams` uniform via `groups_for`).
### Fix steps
1. Require and verify an **`email_verified`** (Google/Entra JWT) / domain-bound claim on every federation
   path; reject if absent/false.
2. Bind federation to the **corporate email domain**; reject external/guest/Slack-Connect senders (or require
   an explicit allow-list).
3. Document email as a **non-authoritative** key and the platform tenant as a hardened precondition
   (no-guests, corporate-domain-only installs).
4. **Teams OBO side-issue** (`12 §4`): the Entra OBO→Okta-audience exchange is a non-standard cross-IdP flow
   and also breaks "teams = Okta groups." Either drop OBO and mint uniformly off the verified-email→Okta
   lookup for all three platforms, or specify the concrete Entra→Okta exchange and which group source feeds
   `teams`.
5. Add inbound-event **idempotency** (gov-review F11): dedupe on the platform event id before enqueue + a
   dead-letter path, so an acked event isn't silently dropped and platform retries don't double-invoke. (This
   rides alongside the federation fix since both touch the channel ingress path.)

### Acceptance criteria
- Negative test: a channel event carrying a colliding external/unverified email is **rejected**, not
  federated.
- Positive test: a verified corporate-domain user federates to the right Okta subject + teams.
- Duplicate inbound event id → single `runtime.invoke`.

---

## Cross-cutting notes
- **Do C1 first and decide its model before the mechanical cleanup** — otherwise you faithfully implement a
  coarse auth model.
- Several fixes share a surface: C4.2 + gov-review F5 (org plane), H1.5 + gov-review F11 (channel ingress),
  C3 dedup + M7. Fix them together.
- Confidence: C1/C3 are provable self-contradictions (high confidence); C2/C4 are correctness/isolation risks
  (high on the mechanism, medium on exploitability given no running code); H1 is a concrete trust gap (high).
  All severities are design-stage — nothing is in production.

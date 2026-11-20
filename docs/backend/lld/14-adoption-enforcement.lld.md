# 14 (LLD) — Adoption & Enforcement (modes, policy surface, shadow-usage measurement)

> **⚠️ Secret custody — as built (feature 014, 2026-11-11):** provider-key custody now runs through a pluggable secrets backend (**AWS Secrets Manager** / LocalStack / `local`), **not** HashiCorp Vault Transit. The `transit/keys/platform-pool` / Vault detail in this LLD is the **superseded design baseline** for the still-unbuilt enforcement path — the *concept* (one platform-scope key path for enforced orgs) holds; only the custody engine changed. See `docs/scope.md` (2026-11-11) and [`../09-security-tenancy.md §9`](../09-security-tenancy.md).

> **Parent:** [`../14-adoption-enforcement.md`](../14-adoption-enforcement.md) (the key-custody spine §1, the
> create-time registration gate §2, the similarity/dedup scanner §3, import/migration §4, MCP §5 + skill §6
> open-registration governance).
> The parent fixes the *stance* — the platform becomes the sole provider-key custodian, agents must register
> before build/publish, imports enter the same funnel — and is unusually honest that **custody alone is
> porous** (teeth need org-level cloud-IAM + network-egress control it cannot build itself). What it leaves
> open is everything a builder hits turning that stance into code: there is **no `enforcement_mode` knob** (it
> is implicitly all-or-nothing), **no rollout/phasing model**, **no exemption/break-glass path**, **no
> adoption/shadow-usage measurement** (the §4 import-funnel buckets are the only signal named), and the
> enforcement **error surface** is one row (`registration.gate`). This doc fills those, and — per the parent's
> own honesty discipline — draws the hard line between what is **enforceable in this codebase** and what is
> **advisory until the org owns IAM + egress**.

## What this adds over parent `14`

| Gap in `14` | Filled here |
|---|---|
| Enforcement is implicitly all-or-nothing; no knob, so you cannot migrate the ~40-agent fleet incrementally | §1 — the `enforcement_mode` enum (`off`\|`monitor`\|`enforce`), org default + per-workspace override, DDL |
| "`POST /provider-keys` disabled/read-only at the workspace tier" (§1) — *where* the check sits, *what* it returns, how invoke resolves a credential under each mode — unspecified | §2 — the three enforcement points + the **enforceable-vs-advisory matrix** (the honest line) |
| Enforcement denial surfaces as *what* HTTP? Only `registration.gate` (409) is in the table | §3 — the enforcement error taxonomy + the "what the api can and cannot deny" honesty note |
| "visibility + mandate" is named as the degraded state (§1) but **no adoption/shadow-usage signal is defined** — you can't report adoption you don't measure | §4 — the adoption + shadow-usage signals, collection (cross-ref `10`), rollup (cross-ref `15`) |
| Nudge / soft-enforcement (`monitor` mode) behavior is absent; the §4 import funnel has no home | §5 — nudges via notifications (`16`), the import-funnel surface |
| Exemptions, break-glass, and the custody **single-point-of-failure** blast radius are unaddressed | §6 — `enforcement_exemptions` DDL, break-glass flow, the pool-key SPOF tradeoff |
| No rollout model for flipping a live org from `off` → `enforce` | §7 — the phased rollout runbook |

Python 3.12, `from __future__ import annotations`, `AsyncSession` on every DB path (`00-conventions.lld.md §4`),
spend quantized via `quantize_usd` (`02 §7` / `00-conv`). Snippets compile (§8). The custody spine, the
registration gate, the dedup scanner, import, MCP/skill governance: **see `14` — not restated.** Errors +
idempotency: [`00-conventions.lld.md`](00-conventions.lld.md). Egress machinery: `09 §10` / `11 §1`. Org
reporting plane: [`15`](../15-org-governance-reporting.md). Telemetry: [`10`](../10-observability-monitoring.md).

---

## 1. Enforcement modes & the policy surface (the knob `14` lacks)

`14 §1` describes a single enforced end-state. A live org with ~40 hand-wired agents (`14 §4`) **cannot** be
flipped there atomically — collapsing every workspace's BYO key into the pool the instant you turn custody on
would break every agent not yet imported. Enforcement is therefore a **mode**, set per org with a per-workspace
override so the fleet migrates one workspace at a time (§7).

```sql
-- 01 §4 is CANONICAL for this DDL; shown here for context. Keep the two in lockstep.
create type enforcement_mode as enum ('off', 'monitor', 'enforce');

-- Per-workspace OVERRIDE of the org default. NULL = inherit the deploy-time org default (below).
alter table workspaces add column enforcement_mode enforcement_mode;   -- nullable: NULL ⇒ inherit
```

- **Org default** is a **deploy-time config value** (`org.config.json` / env), the same deploy-time channel as
  branding (`13 §2`) and the admin group (`15 §1.2`) — **one deployment = one org** (`15 §1`), so there is no
  `org_id` row to store it on. Default ships as `off` (a fresh deploy governs nothing until an admin opts in).
- **Per-workspace override** is the phasing lever: a workspace mid-migration sits at `monitor` while the rest of
  the org is still `off`, and flips to `enforce` only once its agents are imported + registered (§7).

```python
# services/api/enforcement/policy.py
from __future__ import annotations
from enum import Enum


class EnforcementMode(str, Enum):
    OFF = "off"          # no custody, no gate teeth beyond registration record-keeping
    MONITOR = "monitor"  # soft: BYO still allowed, but direct-call signals are collected + owners nudged (§5)
    ENFORCE = "enforce"  # hard: workspace holds no usable provider key; BYO writes rejected (§2/§3)


def effective_mode(ws_override: EnforcementMode | None, org_default: EnforcementMode) -> EnforcementMode:
    """Per-workspace override wins; NULL inherits the deploy-time org default (§1)."""
    return ws_override or org_default
```

- **Setting the mode is an `admin`-only action** (`09 §4`; the app-wide admin claim, `15 §1`). There is no
  per-workspace admin (`15 §1.1`), so a workspace cannot opt *itself* out — only the org admin phases it.
  `PATCH /api/v1/org/workspaces/:id/enforcement` (admin plane, `15 §3` pattern) carries `{mode}` and is
  audited (`enforcement.mode_set`, joins `09 §12` / `01 §4.8`). Monotonic-forward is **not** enforced in
  code — a rollback `enforce → monitor` is a legitimate incident action (§6).

---

## 2. The three enforcement points — and the honest enforceable/advisory line

`14 §1` already states the load-bearing honesty: **custody alone is porous.** This section pins exactly *where*
each control sits relative to the `api` (`08`) and `runtime` (`03`), and marks each **enforceable in this
codebase** vs **advisory / org-infra-dependent**.

| # | Control | Where it sits | Mode it activates in | Enforceable **in this codebase**? |
|---|---|---|---|---|
| **P1** | **BYO provider-key write block** — `POST`/rotate `/provider-keys` at the workspace tier rejected | `api` (`08 §2`) router dependency | `enforce` | **Yes.** A pure api-layer gate. |
| **P2** | **Pool-only credential resolution** — `invoke()` resolves the platform-pool key (`09 §9.1`), never a workspace key | `runtime.invoke` → `secrets.py` (`09 §3.1`) | `enforce` (+ no active exemption, §6) | **Yes.** The workspace has no usable secret to resolve. |
| **P3** | **Registration gate** — build/publish on `registered_at IS NULL` → 409 (`14 §2`) | `api` (`08 §2/§6`) | all modes (independent of custody) | **Yes.** Already shipped in the parent. |
| **P4** | **Direct-provider-call *block*** — a team calling `api.anthropic.com` with a personal/other-account key | **network egress + cloud model-IAM** (`14 §1`, outside this repo) | n/a (org infra) | **No — advisory.** The platform cannot deny a call that never reaches it. |
| **P5** | **Direct-provider-call *detection*** — observing those bypass attempts | **egress proxy** (`09 §10` / `11 §1`, reused one layer out) | `monitor` + `enforce` | **Partial.** Only if the egress proxy exists; it yields a *signal* (§4), not a block. |

> **The honest line (carry `14 §1`'s caveat into code).** P1–P3 are real, self-contained, and shippable here.
> P4 is the one most stakeholders *hear* when we say "enforcement," and it is **not ours to build** — it needs
> network egress ownership + cloud model-IAM centralization (`14 §1`). Without P4, a determined team can still
> paste a personal key into a script; P5 can only *see* it (and only if the egress proxy is deployed). So the
> software-enforceable guarantee is precisely: **"within the platform, the sanctioned path is the only one, and
> every call through it is gated, costed, and audited"** — not "no model call happens outside the platform."
> Say exactly this to stakeholders; §4 measures the gap rather than papering over it.

### 2.1 P1 — the BYO-key write gate

```python
# services/api/routers/provider_keys.py  (08 §2 — admin-tier provider-key routes)
async def create_provider_key(body: ProviderKeyIn, ctx = Depends(require("key.manage"))):
    mode = effective_mode(ctx.workspace.enforcement_mode, ORG_DEFAULT_MODE)
    if mode is EnforcementMode.ENFORCE and not await has_active_exemption(ctx.db, ctx.workspace_id):
        raise ByoDisabled(                                       # 403 enforcement.byo_disabled (§3)
            "workspace BYO provider keys are disabled under enforcement; the platform pool key is the only path",
            detail={"mode": mode.value, "break_glass": "/api/v1/org/exemptions"})
    ...  # off/monitor: unchanged BYO create (09 §9); enforce+exemption: break-glass window (§6)
```

Note this gates the **workspace tier only**. The admin/platform tier still manages the pool key itself
(`transit/keys/platform-pool`, `09 §9.1 / 09 LLD §3.1`) — enforcement removes the *tenant's* key, not the
platform's.

### 2.2 P2 — credential resolution inside `invoke()`

`invoke()` already resolves a provider credential via `secrets.py` (`09 LLD §3`). Under `enforce` there is simply
no workspace-scope provider key to find — `provider_keys` rows point at the **platform-pool** `vault_path`
(`14 §1`, `09 §9.1`). The only mode-aware branch is the exemption window:

```python
# services/runtime/secrets.py (resolution shape; 09 §9.1 owns the DEK scopes)
async def resolve_provider_credential(ctx, *, db) -> "ProviderCredential":
    mode = effective_mode(ctx.workspace_enforcement_mode, ORG_DEFAULT_MODE)
    if mode is EnforcementMode.ENFORCE and not await has_active_exemption(db, ctx.workspace_id):
        return await pool_credential()                 # transit/keys/platform-pool — the one custodied path
    return await workspace_or_pool_credential(ctx)     # off/monitor/exempt: workspace BYO if present, else pool
```

This is the P2/P5 split made concrete: enforcement is a **custody** property (what key exists), not a runtime
request-time denial. `invoke()` never raises an *enforcement* error — a run always has *a* key (pool or, under a
break-glass exemption, the workspace's). Enforcement failures live entirely at the api write boundary (P1) and
the network boundary (P4, not ours).

---

## 3. Enforcement error taxonomy (HTTP surfacing)

Per the `00-conventions.lld.md §1` three-category taxonomy, every enforcement denial that the **platform itself**
produces is **category A** (HTTP-surfacing `AppError`). Shadow usage (P4/P5) is **not an exception at all** — it
is a telemetry signal (§4), because the offending call never entered the api.

| Condition | HTTP | `code` | Class |
|---|---|---|---|
| build/publish on an unregistered agent (`14 §2`, already in `08 §1`) | 409 | `registration.gate` | `RegistrationGate` (`00-conv §1.2`) |
| workspace BYO provider-key create/rotate under `enforce` (P1) | **403** | `enforcement.byo_disabled` | **new** — `ByoDisabled` |
| non-admin attempts to set `enforcement_mode` or grant an exemption | 403 | `access.denied` | `AccessDenied` (`00-conv §1.2`) |
| a direct provider call bypassing the platform (P4) | **—** | **—** | **not an HTTP error** — see below |

```python
# add to api/errors.py alongside the 00-conventions.lld.md §1.2 classes
class ByoDisabled(AccessDenied):            # 403 family; distinct code so the FE can show the break-glass CTA
    code = "enforcement.byo_disabled"
```

> ⚠️ **Amendment to `08 §1`:** add one row — `workspace BYO provider-key write under enforcement → 403
> enforcement.byo_disabled`. It is the only enforcement outcome the parent table doesn't cover
> (`registration.gate` is already there). Modeled as a subclass of `AccessDenied` so it reuses the 403 status;
> the distinct `code` lets the console render a "request break-glass" action (§6) rather than a generic denial.
> *(Now folded into parent `08 §1` as the `403 enforcement.byo_disabled` row.)*

> **What the api can and cannot deny (honesty, restated at the error layer).** Every row above is a denial of an
> action that came *through* the platform. There is deliberately **no** HTTP code for "you called Anthropic
> directly" — the platform never saw that request (P4). Pretending otherwise with, say, a `403 shadow.detected`
> would be a lie: nothing to attach it to. Shadow usage surfaces as a **report line + an owner nudge** (§4/§5),
> never as an error to the bypasser.

---

## 4. Adoption & shadow-usage measurement (the signal `14` never defines)

`14 §1` names the degraded end-state — "visibility + mandate" — but defines no way to *measure* adoption or the
shadow gap. You cannot report adoption you do not collect. Two signal classes, with sharply different reliability:

### 4.1 In-platform adoption signals (reliable — fully ours)

All derive from data the platform already owns; **no new store** — query-time rollup on the `15` org plane
(`15 §3` pattern), consistent with the org-report design.

| Signal | Definition | Source |
|---|---|---|
| `agents_registered` / `agents_total` | registration coverage per workspace | `agents.registered_at` (`14 §2`) |
| `agents_imported` | fleet onboarded via the import funnel (`14 §4`) | import audit (`agent.import`, §5) |
| `platform_spend_30d` | $ of LLM spend that went *through* the platform | `runs.cost_usd` rollup (`10 §6`, quantized) |
| `enforcement_mode` | the per-workspace mode (§1) | `workspaces.enforcement_mode` |
| `exemptions_active` | open break-glass / BYO exemptions (§6) | `enforcement_exemptions` |

### 4.2 Shadow-usage signal (partial — depends on org egress ownership)

The **only** software-visible shadow signal is the **egress proxy's deny log** (P5): a workspace/service identity
attempting to reach a provider endpoint (`api.anthropic.com`, Bedrock/Vertex regional endpoints, …) from outside
the sanctioned egress path. This **reuses the deny-by-default egress machinery** (`09 §10` / `11 §1`) one layer
out — exactly the mechanism `14 §1` points at. Collection follows `10 §4/§8` (provider-resilience telemetry):

```python
# emitted by the egress proxy sidecar, scraped into Prometheus (10 §4). NOT persisted in Postgres —
# telemetry stays in the telemetry stores (10 §2); duplicating it into the primary DB buys nothing.
SHADOW_CALLS = Counter(
    "ac_shadow_provider_calls_total",
    "direct-to-provider calls DENIED at the network egress boundary (attempted platform bypass)",
    ["workspace", "provider"],
)
```

> **The measurement is only as good as the egress deployment.** If P4/P5 infra is **not** in place,
> `ac_shadow_provider_calls_total` is simply **absent** — the shadow denominator is unknown, and the honest
> adoption report reads "platform spend = $X; shadow spend = **unmeasured** (no egress visibility)," never
> "shadow = 0." The ground-truth reconciliation — provider-billing-total minus platform-attributed spend — is an
> **out-of-band finance exercise** (we never read provider billing for cost, `14 §1(a)` / `02 §7`); it is the
> real adoption-gap number and lives outside this codebase. Do not let an absent signal masquerade as full
> adoption.

### 4.3 Rollup — on the `15` org plane

Adoption is an **org-wide, cross-tenant** read, so it belongs on the admin plane via the audited `SECURITY
DEFINER` path (`15 §2`), **not** a per-workspace endpoint. One additive endpoint, modeled on `15 §3`:

```python
# services/api/org/routes.py  (additive to 15 §3 — flag for the 15 LLD to adopt)
@router.get("/api/v1/org/adoption")
async def org_adoption(ctx: PlatformAdminCtx = Depends(require_platform_admin)):   # 15 §2 elevated path
    db = ctx.db                                                                    # platform_report conn (C4)
    rows = await db.execute(text("select * from org_report_adoption()"))           # cross-tenant, definer fn (§4.1)
    shadow = await prometheus_shadow_counts()          # §4.2 — may be empty ⇒ reported as "unmeasured", not 0
    await write_org_audit(db, actor=ctx.principal.subject_id,
                          action="org.report.read", target="org/adoption")         # workspace_id NULL (15 §2.2)
    return project_adoption(rows, shadow)              # per-workspace: mode, registration %, spend, shadow|null
```

Two additive hooks to the siblings (**now folded** into the sibling LLDs):
- **`15` LLD §2.3** homes the `org_report_adoption()` definer fn + the `/api/v1/org/adoption` endpoint (now
  folded); `15 §3.3` compliance **may** still gain a `shadow_usage_detected` flag (true only when
  `ac_shadow_provider_calls_total > 0` for the workspace; **never** inferred from a missing signal).
- **`10` LLD §6** carries the `ac_shadow_provider_calls_total` counter (§4.2) in its egress/provider-resilience
  set (now folded; parent `10 §4/§8`).

---

## 5. Nudges / soft-enforcement (`monitor` mode) + the import funnel

`enforce` is the hard block (§2). `monitor` is the **nudge** rung that makes a phased rollout humane: BYO keys
still work, but the platform *collects* the shadow signal (§4.2) and *tells someone*, so adoption climbs by
persuasion before it is compelled.

- **Nudge channel = owner notifications (`16`), not Alertmanager.** A sustained shadow signal for a workspace
  emits a `shadow_usage` owner notification (`16` LLD §3; distinct from the ops `10 §5` alert — the `10 §8` note
  "ops alert ≠ owner notification" applies). Deduped per `16`'s rules so a chatty workspace nudges once per
  window, not per denied call. *(Now folded: `16` LLD §3 defines the `shadow_usage` kind; `01 §3` enum updated.)*
- **The import funnel is the adoption on-ramp made visible.** `14 §4`'s bulk driver returns per-agent buckets
  `imported | profile_missing | dedup_hit`; surface the roll-up on the `/api/v1/org/adoption` report (§4.3) so
  `profile_missing` is a live punch-list against the `02` profile catalog and `dedup_hit` is the first real
  measurement of fleet duplication (`14 §4`). No new mechanism — just plumb the existing buckets to the report.
- **`monitor` does not gate anything.** Registration (P3) still applies in all modes (it is custody-independent),
  but BYO writes (P1) and pool-only resolution (P2) are **inert** under `off`/`monitor`. This is the whole point:
  you can run the measurement + nudges for weeks with zero risk of breaking a live agent before flipping
  `enforce` (§7).

---

## 6. Exemptions, break-glass, and the custody single-point-of-failure

Collapsing every provider key into one pool (`14 §1`) creates a **blast radius the parent does not name**: the
platform pool key + Vault (`09 §9`) + the egress path become a **single point of failure for *all* LLM usage
org-wide**. If any is down under `enforce`, no agent in any enforced workspace can call a model. That is an
operational reality, not a hypothetical — so enforcement needs a sanctioned, time-boxed, audited escape.

```sql
-- 01 §4 CANONICAL; shown for context. WorkspaceScoped (01 §5 mixin) + RLS (01 §6).
create table enforcement_exemptions (
  id            uuid primary key default gen_random_uuid(),
  workspace_id  uuid not null references workspaces(id),
  agent_id      uuid references agents(id),          -- NULL = workspace-wide exemption
  reason        text not null,                       -- mandatory; shows in the org audit + adoption report
  kind          text not null,                       -- 'break_glass' | 'migration' (phased BYO window, §7)
  granted_by    text not null,                       -- admin OIDC subject (09 §2) — only admin may grant (§1)
  granted_at    timestamptz not null default now(),
  expires_at    timestamptz not null,                -- HARD expiry; no open-ended exemptions
  revoked_at    timestamptz                          -- early manual revoke
);
create index enforcement_exemptions_active
  on enforcement_exemptions (workspace_id)
  where revoked_at is null;                          -- active-lookup index; expiry checked in the predicate (below)
```

```python
# services/api/enforcement/exemptions.py
from __future__ import annotations
from datetime import datetime, timezone

BREAK_GLASS_MAX_TTL_S = 4 * 3600        # design choice: break-glass caps at 4h; migration windows may be longer


async def has_active_exemption(db, workspace_id: str, agent_id: str | None = None) -> bool:
    """True iff an unrevoked, unexpired exemption covers this workspace (or the specific agent). Used by P1 (§2.1)
    and P2 (§2.2). Expiry is evaluated in SQL (now()) so a lapsed exemption needs no sweeper to take effect."""
    row = await db.scalar(text(
        "select 1 from enforcement_exemptions "
        "where workspace_id = :ws and revoked_at is null and expires_at > now() "
        "  and (agent_id is null or agent_id = :ag) limit 1"),
        {"ws": workspace_id, "ag": agent_id})
    return row is not None
```

- **Break-glass flow.** An admin grants a `break_glass` exemption (`POST /api/v1/org/exemptions`, admin plane) →
  P1/P2 fall back to the workspace BYO key for the window → the exemption **auto-lapses** at `expires_at` with no
  sweeper (the predicate is `now()`-relative) → every grant **and** lapse is audited (`enforcement.exemption_grant`,
  `enforcement.exemption_expire`) to the org sink (`15 §2.2`, `workspace_id` NULL) and shows on the adoption
  report as `exemptions_active` (§4.1). Mandatory `reason` means "why is this workspace off the sanctioned path"
  is always answerable.
- **Caps, not open-ended.** Break-glass TTL is bounded (`BREAK_GLASS_MAX_TTL_S`); a longer `migration` window is a
  separate `kind` granted deliberately during rollout (§7). There is **no** indefinite exemption — the whole
  enforcement story collapses if "temporary" BYO becomes permanent.
- **Blast-radius mitigation beyond break-glass** is an ops concern owned elsewhere: pool-key HA, Vault HA
  (`09 §9`), a versioned fallback provider (`02 §6/§8`). This LLD only guarantees a *sanctioned manual* escape;
  it does not make the pool key not-a-SPOF. Say so.

---

## 7. Rollout model (flipping a live org `off` → `enforce`)

The ~40-agent fleet (`14 §4`) migrates **per workspace**, never org-wide atomically. The runbook:

1. **`off` → `monitor`** (org default, or per workspace). Stand up the egress proxy (P5) if available; begin
   collecting the shadow signal (§4.2) and nudging owners (§5). **No agent breaks** — nothing is gated yet.
2. **Import + register the workspace's agents** (`14 §4` bulk driver → `imported`/`profile_missing`/`dedup_hit`).
   Work the `profile_missing` punch-list against `02`; resolve `dedup_hit`s (reuse or audited override, `14 §3`).
   The workspace is "migration-ready" when `agents_registered == agents_total` and no agent depends on a BYO key
   the pool can't cover.
3. **Grant a `migration` exemption** (§6) if a cutover window is needed, then **flip the workspace to `enforce`**
   (§1). P1 blocks new BYO writes; P2 resolves pool-only. Point the `provider_keys` rows at the platform-pool
   `vault_path` (`14 §1`, `09 §9.1`).
4. **Watch the adoption report** (§4.3): `platform_spend_30d` should rise, `ac_shadow_provider_calls_total`
   should fall toward zero. A non-zero shadow count **after** `enforce` is the signal that P4 (network/IAM) is
   missing — i.e. the degraded "visibility + mandate" state (`14 §1`), now *measured* rather than assumed.
5. **Repeat per workspace;** set the org default to `enforce` once the long tail is migrated. Rollback
   (`enforce → monitor`, §1) stays available for incident response.

The deliberate property: at **every** step a live published agent keeps working — `monitor` gates nothing, the
migration exemption covers the cutover, and pool-only resolution (P2) only engages once the pool can serve the
workspace. There is no flag-day.

---

## 8. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as `00`/`02`/`08` LLDs); the `sql`
   blocks are DDL/`select` only (enum, two `alter table`, two `create table`/`index`) and parse as standalone
   statements.
2. **Cross-ref (grep `enforce`/`adoption`/`policy`/`gateway`/`workspace_id`)** against `14` (HLD, read in full),
   `08` LLD §1/§2 (error table + provider-key/admin routes), `10` HLD §2/§4/§8 (telemetry split + provider
   resilience), `15` HLD §1/§2/§3 (admin plane + elevated read + report endpoints), `09` LLD §1/§3 (GUC, DEK
   scopes, `platform-pool`), `07` LLD §1 (`spend_limits`/`cap`), `00-conventions.lld.md §1/§4` (taxonomy,
   `AsyncSession`), and `scope.md` decision log (2026-10-18 P0-1 entry). No contradictions found:
   - `enforcement_mode` is **net-new** (confirmed absent everywhere by grep) — no collision.
   - P2 pool-only resolution matches `09 §9.1` / `09 LLD §3.1` (`transit/keys/platform-pool`); `14 §1(a)`
     cost-attribution-survives is preserved (spend still `runs.cost_usd`, `10 §6`).
   - `registration.gate` reused verbatim from `00-conv §1.2` / `14 §2`; the one new code
     `enforcement.byo_disabled` is flagged as an `08 §1` amendment, not silently assumed present.
   - The honesty line (P4 advisory, shadow measurement degrades to "unmeasured") is carried straight from
     `14 §1`'s own "visibility + mandate" caveat — this LLD *measures* that gap, consistent with the HLD, and
     does not over-promise a block the codebase can't deliver.
3. **Flagged amendments (now folded in the reconciliation pass):** `08 §1` gained the `enforcement.byo_disabled`
   row; `15` LLD §2.3 homes `/api/v1/org/adoption` + `org_report_adoption()` (+ optional `shadow_usage_detected`
   compliance flag); `10` LLD §6 carries `ac_shadow_provider_calls_total`; `16` LLD §3 defines the `shadow_usage`
   notification kind (`01 §3` enum updated).

→ Next: `15-org-governance-reporting.lld.md`.

# 15 (LLD) — Org Governance & Reporting (the admin-plane cross-tenant read)

> **Parent:** [`../15-org-governance-reporting.md`](../15-org-governance-reporting.md) (the app-wide `admin`
> claim, the elevated audited cross-tenant read path, the metadata-only private-tier constraint, the three
> report endpoints, the compliance flags).
> The parent fixes the **model and the altitude**: one org per deployment (no `org_id`, no roster), admin is
> the `ap-platform-admin` claim (`09 §1`), and the one sanctioned crossing is a `SECURITY DEFINER` read on a
> separate `platform_report` connection. What it leaves at HLD altitude is exactly the load-bearing wiring:
> the parent shows **one** report function (`org_report_agents()`), then hand-waves `enrich_estate` as "ALSO
> via definer fns"; it writes the org audit row with a **plain `INSERT`** that the `audit_logs` `FORCE` RLS
> `WITH CHECK` **rejects**; it never states the owner precondition that lets a definer function beat `FORCE`
> RLS; and it leaves the cost-quantization rule, the cache/audit interaction, the MV scale lever, the error
> taxonomy, and the edge cases to the implementer. This doc makes those buildable **on top of the
> `09 §4` org-plane boundary** — it does not re-specify that boundary, it consumes it.

## What this adds over parent `15` (and how it sits on `09 §4`)

| Gap in `15` | Filled here |
|---|---|
| `org_report_agents()` is the only function shown; the estate **enrichment** (active-version label, latest eval verdict, 30-day spend, compliance flags) is prose — "`enrich_estate` … ALSO via definer fns" (`15 §3.1`) with no DDL, and would need RLS-bound joins the `platform_report` role can't run | §2.3 — the three concrete `SECURITY DEFINER` functions (estate **enriched in-function**, spend rollup, compliance), on the `09 §4` template (pinned `search_path`, `REVOKE … public/ac_app` + `GRANT … platform_report`) |
| `15 §2.2` writes the `workspace_id = NULL` audit row with a plain `INSERT` — but `audit_logs` has `FORCE` RLS with `WITH CHECK (workspace_id = current_setting(...))` (`09 §2`), so a `NULL` post-image **fails the check** and the insert is rejected | §5 — the org audit write is itself a `SECURITY DEFINER` function (`org_write_audit()`), the only form that can land a `NULL`-workspace row; `GRANT`ed to `platform_report` only |
| `09 §4` / `15 §2` say the definer function "reads across tenants … does NOT set `app.workspace_id`," but `09 §2.1` puts **`FORCE` RLS** on every scoped table — under `FORCE` the **owner is not exempt**, so the function returns **zero rows** unless its owning role holds `BYPASSRLS`. The precondition is unstated | §2.2 — the definer owner (`ac_migrate`) must hold `BYPASSRLS`; `platform_report` still holds none (its only reach is `EXECUTE` on the functions). Flagged as a clarification to `09 §4.1`. |
| `15 §2` pins `set search_path = public`; `09 §4` hardens the same template to `set search_path = pg_catalog, public` | §2.3 — aligned to the `09 §4` hardened form (the security spec wins) |
| `15 §3` "cache per `(metric, range)` in Redis, short TTL" **and** "audited on every call" are stated independently; their interaction (a **cache hit must still audit**) is undefined | §3.2 + §6.3 — audit is written **first and unconditionally** (the audited event is the *access*, not the computation); the cache holds the already-redacted projection |
| `15 §3` nightly materialized view is "a known lever, not built in P1" — no DDL, no refresh path, no `REFRESH … CONCURRENTLY`-in-transaction caveat | §3.3 — the MV DDL, a definer `org_refresh_estate_mv()`, the worker schedule, and why the base refresh is **non-concurrent** |
| `15 §1` asserts admin "owns all governance … and workspace CRUD" but there is no model of **how org governance reaches a workspace** (no `org_id`, no cascade link) | §1.2 + §4 — governance = **deploy-time org defaults** (seed at provisioning) **+ per-workspace admin override** via the tenant plane; no org-scoped policy table |
| Cost roll-ups "use the same `runs.cost_usd`" but no quantization rule for the **aggregate** (vs the per-row `02 §7` quantize) | §3.1 — `quantize_usd` **once** at the DTO boundary; summing already-quantized 6dp values is exact, so this normalizes type, it is not a second rounding |
| No error taxonomy, idempotency statement, or edge-case register for the org plane | §6 — reuse `00-conventions.lld.md §1`; GET reads need no idempotency key; the edge register (empty org, workspace added mid-report, no-cap-set, private content, permission boundary, cache hit) |

Python 3.12, `from __future__ import annotations`, SQLAlchemy **`AsyncSession`/`AsyncEngine`** (session model
pinned in [`00-conventions.lld.md §4`](00-conventions.lld.md)). SQL is PostgreSQL 16. Snippets compile (§7).
`Principal` / `is_admin` / `require_platform_admin` / `PlatformAdminCtx` / `project_estate_row`: **see `15` —
not restated.** The three DB roles, the `SECURITY DEFINER` template, the `REVOKE/GRANT` boundary, the dedicated
engine, the DB-level isolation test: **see [`09-security-tenancy.lld.md §4`](09-security-tenancy.lld.md) — built
on, not restated.**

---

## 1. The org / governance data model

### 1.1 Hierarchy, and what is actually persisted

**One deployment = one org** (`15 §1`, `13 §1`): the "org" is the deployment itself, not a row. The hierarchy
is `deployment (org) ⊃ workspaces ⊃ resources`, and the **only org-level rows that exist** are `audit_logs`
rows with `workspace_id = NULL` (the carve-out, `09 §2.2` / `15 §2.2`). Concretely, this LLD introduces **no new
table** and honors `15 §1`'s two prohibitions:

- **No `orgs` governance table, no `org_id` column.** `orgs` stays branding-only (`13 §1`); an `org_id`
  dimension would be a column whose value is constant across the deployment.
- **No `org_memberships` / `platform_admins` roster.** `Principal.is_admin` is read live from the
  `ap-platform-admin` claim (`09 §1`, `18 §6`). There is nothing to store, upsert, or RLS-partition.

### 1.2 Governance policy = deploy defaults + per-workspace admin override (the "cascade")

`15 §1` says admin "owns all governance." The load-bearing clarification: **governance is not a stored
org-wide policy that live-cascades** — it is the existing per-workspace policy columns, written through the
tenant plane. Two sources, resolved at two different times:

| When | Mechanism | Lands in |
|---|---|---|
| **Workspace provisioning** | deploy-time **org defaults** in `org.config.json` (the same deploy channel as branding, `13 §2`) seed the new workspace's policy columns | `workspaces.eval_*`, `spend_limits`, default `access_policy`, registration mandate |
| **Any time after** | an **admin override** via the normal tenant-plane admin routes (`08`, §4.2) | the same columns, per workspace |

"Cascade" therefore means **seed-then-override**, not a live org→workspace inheritance edge. There is no
foreign key from an org policy to a workspace policy to keep in sync, and no reconciliation job to drift.

> **Rejected alternative — a singleton `org_policy` row that live-cascades.** It would re-introduce
> org-scoped stored logic that `15 §1` forbids, add a precedence question (org value vs per-workspace override)
> on every policy read, and need a propagation job. Explicit per-workspace columns seeded at provisioning are
> simpler to reason about and already enforced by engines that exist (below). The cost — changing an org
> default does not retroactively move already-provisioned workspaces — is acceptable and is exactly what the
> `/org/compliance` report surfaces.

**Org governance adds no new enforcement engine.** Each lever is enforced where it already is; org governance
sets the knob and *reads across workspaces to verify it* (§2–§3):

| Governance lever | Enforced by (existing) |
|---|---|
| eval policy (min cases, quality threshold, efficiency budget) | the publish gate (`05 §3/§5`) |
| monthly spend cap | the budget guard (`07 §3`) |
| per-resource access policy (visibility/role/team) | invoke-time access resolution (`09 §5`) |
| registration mandate (`agents.registered_at`) | the create-time registration gate (`14`) |
| provider-key custody | the gateway + Vault envelope (`02`, `09 §9`) |

### 1.3 Report "definitions" are code, not rows

`15 §3` fixes **three** built-in reports and chooses **query-time aggregation, no rollup table in P1**. So a
"report definition" is a code-level spec, not a user-authored stored entity:

```python
# services/api/org/reports.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import timedelta


@dataclass(frozen=True)
class ReportSpec:
    key: str                 # "agents" | "spend" | "compliance" — the /api/v1/org/<key> suffix
    fn: str                  # the SECURITY DEFINER function that backs it (§2.3)
    default_window: timedelta | None   # None = point-in-time estate; else the spend/compliance lookback
    cache_ttl_s: int         # Redis TTL for the projected rows (§3.2)


ORG_REPORTS: dict[str, ReportSpec] = {
    "agents":     ReportSpec("agents", "org_report_agents()", None, cache_ttl_s=30),
    "spend":      ReportSpec("spend", "org_report_spend(:since)", timedelta(days=30), cache_ttl_s=60),
    "compliance": ReportSpec("compliance", "org_report_compliance(:since)", timedelta(days=30), cache_ttl_s=30),
    "adoption":   ReportSpec("adoption", "org_report_adoption()", None, cache_ttl_s=60),   # 14 §4.3 (adoption/shadow gap)
}
```

User-saved report *views* (a named filter set) are a P2 nicety and would be a tenant-plane convenience row, not
an org-plane primitive; out of scope here.

---

## 2. The cross-workspace read plane — building on `09 §4`

This section **consumes** the `09 §4` boundary. It does not redefine the three roles, the `REVOKE/GRANT`
pattern, the dedicated engine, or the DB-level isolation test — those are `09 §4.1`–`§4.4`. It adds the
functions `15` actually needs and resolves two gaps that otherwise make `09 §4`'s stated behavior not work.

### 2.1 The dedicated engine (restating the `09 §4.3` contract we build on)

The org plane runs on a **separate `AsyncEngine`** authenticated as `platform_report`, never the request
`ac_app` session, and **never sets `app.workspace_id`** (`09 §4.3`). Because the GUC is unset, the `09 §2`
tenant RLS predicate is not in play on this connection — the `SECURITY DEFINER` functions (§2.3) are the only
rows it can reach, and a mistaken RLS-bound query on this role returns empty (the fail-safe the `15 §2`
isolation test asserts).

```python
# services/api/org/plane.py — the org plane's single DB entrypoint. Built on 09 §4.3 (_org_engine).
from __future__ import annotations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

# DSN authenticates as platform_report (its own creds) — the only role with EXECUTE on org_report_* (09 §4.2).
# REPEATABLE READ so an endpoint that composes several functions sees ONE snapshot (§2.5); single-function
# endpoints are already consistent (one statement = one snapshot).
_org_engine = create_async_engine(
    settings.org_report_dsn,
    isolation_level="REPEATABLE READ",
    pool_pre_ping=True,
)


async def _org_read(sql: str, params: dict | None = None) -> list[dict]:
    """Run one org report query on the platform_report connection. No app.workspace_id is ever set (09 §4.3)."""
    async with _org_engine.connect() as conn:
        async with conn.begin():
            rows = (await conn.execute(text(sql), params or {})).mappings().all()
    return [dict(r) for r in rows]
```

This is a deliberate, narrow exception to the `00-conventions.lld.md §4.1` "one `AsyncSession` per request"
rule: the org plane is a **read-only** path on its own engine with no ORM identity map or RLS GUC to manage, so
a plain `AsyncConnection` (as in `09 §4.3`) is the right altitude. No tenant request ever touches `_org_engine`.

### 2.2 The precondition `09 §4` and `15 §2` both omit — the definer owner needs `BYPASSRLS`

`09 §2.1` puts **`FORCE` ROW LEVEL SECURITY** on every scoped table. Under `FORCE`, PostgreSQL applies RLS to
the **table owner too** — the normal "owner is exempt" rule is turned off. A `SECURITY DEFINER` function owned
by `ac_migrate` runs with `ac_migrate`'s rights, but if `ac_migrate` is merely the owner it is **still forced
under RLS**, and with `app.workspace_id` unset the policy predicate is `workspace_id = NULL` → **zero rows**.
That would silently return an empty estate — the exact failure the plane exists to avoid.

**Decision (precondition, resolves the gap):** the definer **owner role must hold `BYPASSRLS`.** We keep
`ac_migrate` as the owner (per `09 §4`) and grant it `BYPASSRLS`; `BYPASSRLS` overrides `FORCE`, so the
functions read across all workspaces. `ac_migrate` is offline, DDL-only, never on the request path (`09 §4.1`),
so the attribute is not reachable by any request. **`platform_report` still holds no `BYPASSRLS`** — its only
cross-tenant reach is `EXECUTE` on the definer functions, exactly as `09 §4.1` intends.

```sql
-- Offline, in the role migration. The attribute that makes the SECURITY DEFINER functions actually cross the
-- FORCE-RLS tables (09 §2.1). ac_migrate is DDL-only and never on the request path (09 §4.1).
alter role ac_migrate with bypassrls;
-- ac_app and platform_report are created WITHOUT bypassrls (09 §4.1) — unchanged.
```

> ⚠️ **Clarification to `09 §4.1`.** The role table's `ac_migrate` cell reads `BYPASSRLS? → n/a (owner)`. Under
> the universal `FORCE` RLS of `09 §2.1`, "owner" is **not** sufficient — the definer owner must hold
> `BYPASSRLS` or the cross-tenant functions return zero rows. The cell should read `yes (required to overcome
> FORCE; offline DDL role only)`. No behavior `09 §4` *states* changes; this is the attribute that makes that
> behavior real. **(Now folded into `09` LLD §4.1: the `ac_migrate` cell reads `yes — required to overcome the
> §2 FORCE RLS`, with a rationale paragraph — 2026-10-25 reconciliation.)** An equivalent alternative is a dedicated
> `ac_report_definer` owner holding `BYPASSRLS` distinct from `ac_migrate`; we keep one owner for fewer moving
> parts.

### 2.3 The report functions (on the `09 §4` template)

Four functions (the three built-in reports + the adoption rollup `14` homes here, §2.3(4)), each on the
`09 §4.2` template verbatim — `language sql stable security definer`, **pinned
`search_path = pg_catalog, public`** (the `09 §4` hardened form, not `15 §2`'s `public`), then
`REVOKE … FROM public`, `REVOKE … FROM ac_app`, `GRANT EXECUTE … TO platform_report`. The estate function
**folds the enrichment in** (active version, latest eval verdict, 30-day spend) so there is no separate
RLS-bound `enrich_estate` step `platform_report` could not run — resolving the `15 §3.1` hand-wave.

```sql
-- services/api/migrations/org_report_fns.sql — all owned by ac_migrate (§2.2). METADATA/aggregate only;
-- no system_prompt, run I/O, or eval-case bodies are ever selected (private-tier constraint, 15 §2.3 / §3.4).

-- (1) The enriched estate. One row per agent across every workspace. Replaces 15 §2's bare function + the
--     undefined enrich_estate: the joins live INSIDE the definer fn, so platform_report needs no table grants.
create or replace function org_report_agents()
  returns table (
    agent_id uuid, workspace_id uuid, owner_subject text, status agent_status,
    active_version_label text, eval_verdict text, drift drift_state,
    visibility text, registered_at timestamptz, spend_30d numeric
  )
  language sql stable security definer
  set search_path = pg_catalog, public
as $$
  select a.id, a.workspace_id, a.owner_subject, a.status,
         av.label,
         er.verdict,
         a.drift,
         a.access_policy->>'visibility',          -- policy tier; drives the metadata-only redaction (§3.4)
         a.registered_at,                         -- 14 (P0-1); null = unregistered (compliance, §2.3 of parent)
         coalesce(sp.spend_30d, 0)
  from agents a
  left join agent_versions av on av.id = a.active_version_id
  left join lateral (
    select verdict from eval_runs er
    where er.version_id = a.active_version_id
    order by er.started_at desc
    limit 1
  ) er on true
  left join lateral (
    select sum(r.cost_usd) as spend_30d from runs r
    where r.agent_id = a.id and r.created_at >= now() - interval '30 days'
  ) sp on true;
$$;

-- (2) Spend rollup by workspace / agent / model (15 §3.2), parameterized window.
create or replace function org_report_spend(p_since timestamptz)
  returns table (workspace_id uuid, agent_id uuid, model text, usd numeric, runs bigint)
  language sql stable security definer
  set search_path = pg_catalog, public
as $$
  select r.workspace_id, r.agent_id, (s.attrs->>'model') as model,
         sum(r.cost_usd) as usd, count(*)::bigint as runs
  from runs r
  left join spans s on s.run_id = r.id and s.kind = 'model'
  where r.created_at >= p_since
  group by rollup (r.workspace_id, r.agent_id, (s.attrs->>'model'));   -- ws / agent / model subtotals, one scan
$$;

-- (3) The exceptions report (15 §3.3). One row per NON-compliant agent, with the four flags.
create or replace function org_report_compliance(p_since timestamptz)
  returns table (
    agent_id uuid, workspace_id uuid, owner_subject text, visibility text,
    unregistered boolean, unevaluated boolean, over_budget boolean, no_owner boolean
  )
  language sql stable security definer
  set search_path = pg_catalog, public
as $$
  with ws_spend as (
    select workspace_id, sum(cost_usd) as spend
    from runs where created_at >= p_since group by workspace_id
  )
  select a.id, a.workspace_id, a.owner_subject, a.access_policy->>'visibility',
         (a.registered_at is null)                                              as unregistered,
         not exists (select 1 from eval_runs er
                     where er.version_id = a.active_version_id and er.verdict = 'pass') as unevaluated,
         -- no cap set ⇒ Infinity ⇒ never over budget (deliberate; "no cap" ≠ "over budget", §6.3)
         coalesce(ws.spend, 0) >= coalesce(sl.monthly_budget_usd, 'Infinity'::numeric) as over_budget,
         (a.owner_subject is null or a.owner_subject = '')                      as no_owner
  from agents a
  left join ws_spend ws on ws.workspace_id = a.workspace_id
  left join spend_limits sl on sl.workspace_id = a.workspace_id
  where a.registered_at is null
     or not exists (select 1 from eval_runs er
                    where er.version_id = a.active_version_id and er.verdict = 'pass')
     or coalesce(ws.spend, 0) >= coalesce(sl.monthly_budget_usd, 'Infinity'::numeric)
     or (a.owner_subject is null or a.owner_subject = '');
$$;

-- (4) Adoption rollup per workspace (14 §4 / §4.3 — flagged by the 14 LLD, homed here). Registration coverage,
--     enforcement posture, active break-glass exemptions, 30-day platform spend. The shadow-usage denominator is
--     a TELEMETRY signal (ac_shadow_provider_calls_total, 10 LLD §6), joined in the router (§3.2), NEVER from
--     Postgres (14 §4.2) — an absent signal is reported "unmeasured", never 0.
create or replace function org_report_adoption()
  returns table (
    workspace_id uuid, agents_total bigint, agents_registered bigint,
    enforcement_mode enforcement_mode, exemptions_active bigint, platform_spend_30d numeric
  )
  language sql stable security definer
  set search_path = pg_catalog, public
as $$
  select w.id,
         count(a.id)::bigint                          as agents_total,
         count(a.registered_at)::bigint               as agents_registered,   -- 14 (P0-1)
         w.enforcement_mode,                                                   -- 14 §1 (nullable ⇒ org default)
         coalesce(ex.n, 0)                            as exemptions_active,
         coalesce(sp.spend_30d, 0)                    as platform_spend_30d    -- runs.cost_usd, quantized at DTO (§3.1)
  from workspaces w
  left join agents a on a.workspace_id = w.id
  left join lateral (
    select count(*)::bigint as n from enforcement_exemptions e               -- 14 §6
    where e.workspace_id = w.id and e.revoked_at is null and e.expires_at > now()
  ) ex on true
  left join lateral (
    select sum(r.cost_usd) as spend_30d from runs r
    where r.workspace_id = w.id and r.created_at >= now() - interval '30 days'
  ) sp on true
  group by w.id, w.enforcement_mode, ex.n, sp.spend_30d;
$$;
```

The `/api/v1/org/adoption` endpoint follows the §3.2 shape exactly (audit-first, `_cached_or_read`, the
`platform_report` engine), with one addition: after the cross-tenant read it joins the Prometheus shadow counts
(`prometheus_shadow_counts()`, `10` LLD §6) — reported per workspace as a number **or `null`/"unmeasured"** when
the egress proxy is absent (`14` LLD §4.2), never `0`. The import-funnel buckets (`imported`/`profile_missing`/
`dedup_hit`, `14 §4`) ride the same projection. `14` LLD §4.3 defines the caller; this is its definer-fn home.

### 2.4 The grant/revoke boundary (restating `09 §4.2`, applied to every org function)

Every org function — the four report fns above **and** the audit writer (§5) — gets the identical `09 §4.2`
boundary: default-deny, then grant only the reader role.

```sql
do $$
declare f text;
begin
  foreach f in array array[
    'org_report_agents()',
    'org_report_spend(timestamptz)',
    'org_report_compliance(timestamptz)',
    'org_report_adoption()',                    -- §2.3 (4) — 14 §4.3
    'org_write_audit(text,text,text,jsonb)',    -- §5
    'org_refresh_estate_mv()'                   -- §3.3 (P2)
  ] loop
    execute format('revoke all on function %s from public', f);
    execute format('revoke all on function %s from ac_app', f);        -- the normal app role explicitly cannot call it
    execute format('grant execute on function %s to platform_report', f);
  end loop;
end $$;
```

An SQLi or logic bug on an `ac_app` connection cannot invoke any of these — the privilege is not there. That is
the DB-level control `09 §4.4` tests (`test_app_role_cannot_execute_org_functions`), and the `15 §2` isolation
addition (`/api/v1/org/*` is the *only* multi-workspace path, and every crossing writes an audit row).

### 2.5 Read consistency — workspace added mid-report

A single report function is one statement → one MVCC snapshot → a workspace/agent inserted concurrently is
either wholly present or wholly absent, never half. For an endpoint that composes **several** functions, the
`_org_engine` is pinned to **`REPEATABLE READ`** (§2.1), so all functions in the one `conn.begin()` see one
snapshot. A workspace added after the snapshot simply shows up on the next read — there is no partial estate.

---

## 3. Report queries & aggregation

**On-demand in P1** (`15 §3`): aggregate in SQL (the §2.3 functions), cache the projected rows in Redis with a
short TTL, materialize nightly only if the estate outgrows a synchronous scan (§3.3).

### 3.1 `quantize_usd` on the spend rollup (the cost-precision rule)

`runs.cost_usd` is `numeric(12,6)`, already quantized **once** at attach time by the gateway
(`02 §7` `quantize_usd`, `ROUND_HALF_EVEN`). A `SUM` of already-quantized 6-dp values is **exact** — `02 §7`
says "never re-quantize downstream (summing already-quantized values is exact)." So:

- The SQL `sum(cost_usd)` returns an exact 6-dp `numeric`. **Do not** quantize per row before summing.
- At the **DTO boundary** we call `quantize_usd` **once** on the aggregate — purely to normalize type/scale to
  the `Decimal`/6-dp contract. At 6 dp this is **idempotent** (a no-op rounding), not a second rounding, so it
  does not violate the `02 §7` rule.

```python
# services/api/org/project.py
from decimal import Decimal
from runtime.cost import quantize_usd    # 02 LLD §7 — Decimal, 6dp, ROUND_HALF_EVEN


def project_spend_row(r: dict) -> dict:
    return {
        "workspace": r["workspace_id"], "agent": r["agent_id"], "model": r["model"],
        "usd": str(quantize_usd(r["usd"] or Decimal(0))),   # idempotent at 6dp; normalizes the aggregate's type
        "runs": r["runs"],
    }
```

`spend_30d` on the estate row is quantized the same way in `project_estate_row` (`15 §2.3`).

### 3.2 On-demand path + cache, and audit-first

The audited event is the **admin access**, not the computation — so the audit row is written **first and
unconditionally**, even when the response is served from cache. The cache holds the **already-redacted
projection** (metadata-only for private, §3.4), so a cache hit can never serve un-redacted content.

```python
# services/api/org/routes.py
async def _cached_or_read(redis, cache_key: str, loader):
    hit = await redis.get(cache_key)
    if hit is not None:
        return _deserialize(hit)
    rows = await loader()
    await redis.set(cache_key, _serialize(rows), ex=_ttl(cache_key))
    return rows


@router.get("/api/v1/org/agents")
async def org_agents(ctx: "PlatformAdminCtx" = Depends(require_platform_admin)):
    # Audit BEFORE the read, and regardless of cache outcome (§6.3). workspace_id = NULL (§5).
    await _org_write_audit(actor=ctx.principal.subject_id, action="org.report.read", target="org/agents")
    rows = await _cached_or_read(
        ctx.redis, "org:agents:v1",
        lambda: _org_read("select * from org_report_agents()"),       # the ONE cross-tenant read (§2)
    )
    return [project_estate_row(r) for r in rows]                      # metadata-only for private (§3.4)
```

`require_platform_admin` (`15 §2`) is the gate: no `?workspace=` selection, no `SET LOCAL app.workspace_id`,
and `ctx.db`/`ctx.redis` are the `platform_report` engine — never the RLS-bound app session. The spend and
compliance routes are identical in shape, differing only by `ReportSpec` (§1.3) and the `:since` bind.

### 3.3 The P2 scale lever — a nightly materialized view

If a synchronous cross-tenant scan outgrows the request budget (`00 §5`), precompute the estate nightly. The MV
and its refresh are owned by `ac_migrate` and read **through the same definer surface**, so the §2.4 `EXECUTE`
boundary is unchanged.

```sql
-- P2 only. The enriched estate, snapshotted. Owned by ac_migrate (§2.2) so it inherits the cross-tenant reach.
create materialized view org_estate_mv as select * from org_report_agents();

-- Refresh is wrapped in a definer fn so platform_report (not the MV owner) can trigger it under the §2.4 boundary.
create or replace function org_refresh_estate_mv()
  returns void language plpgsql security definer set search_path = pg_catalog, public
as $$
begin
  -- NON-concurrent: brief exclusive lock, run nightly off-peak by the worker (00 §2). REFRESH ... CONCURRENTLY
  -- cannot run inside a function/transaction, and org views are admin-only + staleness-tolerant (15 §3), so the
  -- short lock is acceptable. The upgrade to CONCURRENTLY needs a unique index on the MV AND the worker issuing
  -- REFRESH outside any transaction on a privileged role — deferred until the lock window is a measured problem.
  refresh materialized view org_estate_mv;
end;
$$;
```

When the MV is active, `org_report_agents()`'s caller reads `org_estate_mv` instead; spend/compliance stay
on-demand (they carry a `:since` window the MV can't precompute for every range).

### 3.4 Metadata/content split at projection (enforcing `15 §2.3`)

The definer functions already **select only metadata** — no `system_prompt`, version-snapshot internals, run
I/O, traces, or eval-case bodies. `project_estate_row` (`15 §2.3`) assembles metadata only. Content for a
`private`-tier resource is never a side effect of the estate read; it is the separate, separately-audited
`org.report.content_read` action (`15 §1.1/§2.3`), out of this projection. Because the cache stores the
projected rows (§3.2), redaction is upstream of the cache and cannot be bypassed by a cache hit.

---

## 4. Governance enforcement surface & admin altitude

### 4.1 What admin sets, and how it reaches a workspace

Admin writes governance **through the tenant plane**, one workspace at a time, at the admin altitude already
defined in `08`. There is no org-plane write surface beyond the audit sink (§5):

| Lever | Route (admin altitude) | Column / sink | Enforced by |
|---|---|---|---|
| eval policy | `PUT /api/v1/workspaces/:id/eval-policy` | `workspaces.eval_*` (`01 §4.1`) | publish gate (`05 §3/§5`) |
| spend cap | `PUT /api/v1/workspaces/:id/limits` | `spend_limits.monthly_budget_usd` (`01`) | budget guard (`07 §3`) |
| access-policy defaults | `PUT /api/v1/{res}/:id/access-policy` | `access_policy jsonb` (`01`, `12` for channels) | invoke access (`09 §5`) |
| provider keys | `POST /api/v1/provider-keys` (+ rotate/revoke) | Vault envelope (`09 §9`) | gateway (`02`) |
| registration mandate | deploy config (`14`) | `agents.registered_at` gate | registration gate (`14`) |

The §1.2 "cascade" is these routes plus the deploy-time seed — no new mechanism. Org governance's distinct
contribution is the cross-workspace **read** (§2–§3) that tells admin *which* workspaces are off-policy.

### 4.2 Admin API altitude

The org reports sit **above** the workspace planes (`08 §1`, `/v1/branding` precedent): `Auth = P`
(console session + `ap-platform-admin` claim, `08 §7`), **no `?workspace=`**, routed through `_org_engine`
(`platform_report`), audited on every call. This is `08`'s already-cataloged `/api/v1/org/*` surface; this LLD
does not add endpoints, it specifies the three the parent lists. A tenant-plane admin action (e.g. the
`PUT`s in §4.1) still runs on the **`ac_app`** request session under RLS — admin's cross-tenant reach is
**only** the three `/org/*` reads, nowhere else.

---

## 5. Org-plane audit — the `workspace_id = NULL` write is itself a definer function

`15 §2.2` writes the org audit row with a plain `INSERT … values (NULL, …)`. That **cannot work** on any
RLS-bound connection: `audit_logs` is in the `FORCE`-RLS loop (`09 §2.2`), and its `WITH CHECK
(workspace_id = current_setting('app.workspace_id', true)::uuid)` evaluates `NULL = <anything>` → `NULL`
(never `true`) → the insert is **rejected**. And on the `platform_report` connection the GUC is unset anyway.

**Decision:** the org audit write is a `SECURITY DEFINER` function owned by `ac_migrate` (which holds
`BYPASSRLS`, §2.2), so the `WITH CHECK` does not apply and the `NULL`-workspace row lands. It is `GRANT`ed to
`platform_report` only (§2.4), so the audit trail for the one cross-tenant path is itself on the boundary.

```sql
create or replace function org_write_audit(p_actor text, p_action text, p_target text, p_meta jsonb)
  returns void language sql security definer set search_path = pg_catalog, public
as $$
  insert into audit_logs (workspace_id, actor, action, target, meta)
  values (null, p_actor, p_action, p_target, coalesce(p_meta, '{}'::jsonb));  -- NULL ws = org-level (09 §2.2 carve-out)
$$;
```

```python
# services/api/org/audit.py — replaces the 15 §2.2 plain INSERT (which the audit_logs WITH CHECK rejects).
async def _org_write_audit(*, actor: str, action: str, target: str, meta: dict | None = None) -> None:
    async with _org_engine.begin() as conn:                 # platform_report connection (§2.1)
        await conn.execute(
            text("select org_write_audit(:a, :act, :t, cast(:m as jsonb))"),
            {"a": actor, "act": action, "t": target, "m": _json(meta or {})},
        )
```

> ⚠️ **Amendment to `15 §2.2`.** Its `write_org_audit` body (`insert into audit_logs … values (NULL, …)` on
> `db`) is not buildable against the `09 §2.2` `audit_logs` `FORCE`-RLS `WITH CHECK`; the buildable form is the
> `org_write_audit()` `SECURITY DEFINER` function above. The `01 §4.8` expand/contract making
> `audit_logs.workspace_id` nullable + the tenant-policy carve-out (so `NULL` rows are invisible to tenant
> planes) remains exactly as `15 §2.2` states. New actions unchanged: `org.report.read`,
> `org.report.content_read`, `org.admin.{grant,revoke}`. **(Now folded into parent `15 §2.2`: its
> `write_org_audit` calls the `org_write_audit()` definer fn, and §2's `search_path` is hardened to
> `pg_catalog, public` — 2026-10-25 reconciliation.)**

The `org.report.content_read` action (the only path to `private` content, `15 §2.3`) writes the same way with
its own action string and the resource id in `target`, on its own endpoint — never folded into a report read.

---

## 6. Error taxonomy, idempotency, edge cases

### 6.1 Errors (reuse `00-conventions.lld.md §1`)

The org plane surfaces exactly two `AppError`s (category A), both from `require_platform_admin`:

| Condition | Class (`00-conventions §1.2`) | HTTP |
|---|---|---|
| no session | `AuthRequired` | 401 `auth.required` |
| session without the `ap-platform-admin` claim (incl. a workspace publisher/builder) | `AccessDenied` | 403 `access.denied` |

> **Reconcile `Forbidden` → `AccessDenied`.** `15 §2`/`09 §4.3` write `raise Forbidden("…")`; under
> `00-conventions §1` that is `AccessDenied` (403 `access.denied`). Same status, same envelope — the LLD class
> name is `AccessDenied`. No new class is needed for the org plane.

A `platform_report` connection that somehow issued an `ac_app`-level call hitting an org function it can't
execute surfaces as PostgreSQL `InsufficientPrivilege` → the generic `unhandled → 500 internal`
(`00-conventions §1`, no stack to client); this is a caught bug, not a client-facing contract, and is exactly
what the `09 §4.4` isolation test prevents from ever reaching production.

### 6.2 Idempotency

The org reports are **GET** (read-only) — `00-conventions.lld.md §3` idempotency is for the published *invoke*
write path (a run that bills and persists); it does not apply. There is no `Idempotency-Key` on `/org/*`. The
only writes the plane performs are the append-only audit rows, which are **deliberately not deduped**: each
admin access is its own event, so two identical reads correctly produce two `org.report.read` rows. Re-running
a report is naturally idempotent on the data (it only reads).

### 6.3 Edge-case register

| Edge | Behavior |
|---|---|
| **Empty org** (no workspaces / no agents) | functions return 0 rows → endpoints return `[]`, HTTP 200. No special case. |
| **Workspace/agent added mid-report** | single-statement function = one MVCC snapshot (§2.5); the row is wholly in or out. Appears on the next read. |
| **Workspace with no spend cap** | `over_budget` uses `coalesce(cap, Infinity)` → never flagged. "No cap" ≠ "over budget" — deliberate (§2.3). |
| **Private-tier agent** | metadata appears in the estate; content only via `org.report.content_read` (separate audited action, §3.4/§5). |
| **Permission boundary (workspace-admin vs org-admin)** | there is **no per-workspace admin** (claim model, `scope.md` 2026-10-22). The top per-workspace role (`publisher`) is **not** admin → `/org/*` → 403. Only the app-wide `ap-platform-admin` claim passes. |
| **Admin on the tenant plane** | admin editing a single workspace runs on the `ac_app` request session under RLS (§4.2) — it does **not** get cross-tenant reads there; only `/org/*` crosses. |
| **Cache hit** | the `org.report.read` audit row is still written (audit-first, §3.2) — the audited event is the access, not the computation. |
| **Stale MV (P2)** | `org_estate_mv` is up to one refresh cycle behind; spend/compliance stay on-demand, so a freshly-registered or freshly-evaluated agent drops off compliance on the next read with no reconciliation (`15 §3.3`). |

---

## 7. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as `01`/`05`/`09` LLD); SQL blocks
   checked against PostgreSQL 16 (`security definer`, pinned `search_path`, `rollup`, `lateral`,
   `'Infinity'::numeric`, `do $$ … $$`, `plpgsql refresh materialized view`).
2. **Built on `09 §4`, not duplicated** — the three roles, the `SECURITY DEFINER` template, the
   `REVOKE public/ac_app` + `GRANT platform_report` boundary, the dedicated engine, and the DB-level isolation
   test are **referenced** (`09 §4.1`–`§4.4`), not restated. New functions (`org_report_agents/spend/compliance`,
   `org_write_audit`, `org_refresh_estate_mv`) all follow the `09 §4.2` template and reuse the §2.4 boundary.
3. **Cross-ref** — against `15` read in full (`org_report_agents`, `enrich_estate`, `write_org_audit`, the
   metadata/content split, the three endpoints, compliance flags), `09 §2` (`FORCE` RLS + `WITH CHECK`),
   `09 §4` (the boundary), `00-conventions §1` (error classes), `00-conventions §3/§4` (idempotency scope,
   `AsyncSession`), `02 §7` (`quantize_usd`), `07 §3` (`spend_limits`/budget), `08 §7` (admin altitude,
   `/org/*` catalog), `10 §6` (`runs.cost_usd` rollup / `date_trunc`), `14` (`registered_at`/`owner_subject`),
   `13 §1/§2` (one deploy = one org, deploy-time config), `scope.md` decision log (P0-2 org governance,
   claim-based admin 2026-10-22).
4. **Contradiction scan** (`platform_report`, `org`, `workspace_id`, `SECURITY DEFINER`, `EXECUTE`) against
   `09` LLD + HLD + `scope.md`: consistent, with **two surfaced gaps resolved in favor of the parents' stated
   intent** (flagged, not silently reversed):
   - `09 §4.1` `ac_migrate BYPASSRLS = "n/a (owner)"` is **insufficient** under `09 §2.1` `FORCE` RLS; §2.2
     sets the required `BYPASSRLS` so the definer functions actually cross tenants. Flagged as a `09 §4.1`
     clarification.
   - `15 §2.2`'s plain `NULL`-workspace `INSERT` is **rejected** by the `audit_logs` `WITH CHECK`; §5 makes the
     org audit write a `SECURITY DEFINER` function. Flagged as a `15 §2.2` amendment.
5. **Resolved inconsistency** — `15 §2` previously pinned `set search_path = public` while `09 §4` pins
   `pg_catalog, public`; this doc uses the `09 §4` hardened form and the parent `15 §2` DDL has now been updated
   to match (2026-10-25 reconciliation; cosmetic/security-hardening, no behavior change). The MV→CONCURRENTLY
   upgrade path is left as a P2 note, not built.

→ Builds on: `09-security-tenancy.lld.md §4`. Consumes: `08 §7` (altitude), `02 §7` (`quantize_usd`),
`14` (registration fields). Flags for parents: `09 §4.1` (`BYPASSRLS`), `15 §2.2` (audit via definer).

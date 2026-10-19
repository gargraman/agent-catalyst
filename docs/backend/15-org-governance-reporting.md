# 15 — Org Governance & Reporting (the platform-admin plane)

> Closes the org-governance gap. Today `09 §10` / `10 §6,§10` dashboards are **per-workspace or
> operator-only**, and `09 §4` has **no role that spans workspaces** — so leadership has no org-wide view
> of the agent estate (every agent, who owns it, is it evaluated, what does it cost, is it compliant).
> Both `09 §7` and `01 §6` already *name* an "elevated cross-workspace read path (org dashboards) …
> separate, explicitly-audited" path but neither specifies it; **this doc specifies it.** Builds on
> `spec.md §B12` (audit), the auth planes (`09 §1`), per-resource policy (`access-policy.ts`, `09 §5`),
> the metrics-rollup pattern (`08 §7`, `10 §6`), and the registration fields (`agents.registered_at` /
> `owner_subject`) from `14`. **Design only — nothing here is built** (no `services/`).

## 1. The platform-admin plane

The three auth planes (`09 §1`) are all **tenant-scoped**: a console session, a caller token, and a
channel each resolve to exactly one `workspace_id`, and everything they can read is partitioned by it
(guard + RLS, `09 §3,§7`). Org governance is the one capability that is *deliberately not* tenant-scoped
— it must see across every workspace at once. We model it as a **fourth plane** sitting **above** the
three tenant planes, mirroring how `/v1/branding` already sits "above the workspace planes" (`08 §1`).

**One deployment = one org.** `13` onboards a new organization by **redeploy** (org config baked at
build/deploy time); `orgs` is **branding-only** (`13 §1`). So "platform admin" is a **platform /
deployment-wide** role, not an org-scoped one. Concretely:

- **Do not** introduce an `org_memberships` table or any `org_id`-scoped logic — there is exactly one org
  per deployment, so an `org_id` dimension would be a column that is always the same value.
- Membership is a minimal, **elevated** (not `WorkspaceScoped`) table:

```sql
-- services/api/migrations/platform_admins.sql
-- Deployment-wide governance roster. NOT WorkspaceScoped (no workspace_id), NOT RLS-partitioned — it is
-- the one roster that is intentionally global. Kept separate from `memberships` (01 §4.1) so an elevated
-- grant can never be confused with a per-workspace role, and so RLS never applies to it.
create table platform_admins (
  subject    text primary key,              -- OIDC subject (org Okta user id), same identity source as memberships
  granted_at timestamptz not null default now(),
  granted_by text not null                  -- subject of the platform_admin who granted this (or 'deploy' at bootstrap)
);
```

- **Optionally Okta-group-backed.** Consistent with the **2026-10-15 app-managed-membership decision**
  (`09 §2.1`: the app roster is the system of record; role is *not* group-encoded), a deployment MAY
  auto-upsert `platform_admins` from a designated Okta group read off the OIDC `groups` claim — but the
  table stays authoritative and the role is an app attribute, never derived from a group name at
  authorization time.

### 1.1 Extend the `09 §1` auth-plane table (3 → 4)

Add one row. The new plane is **read-only — governance, not mutation**; it never publishes, edits, or
invokes.

| Plane | Who | Credential | Authorization basis | Used by |
|---|---|---|---|---|
| **Platform-admin** (`P`) | a platform/deployment-wide admin (leadership / central ops) | OIDC session (`09 §2`) **+** `platform_admins` membership | **not** workspace `memberships`; the global `platform_admins` roster — **deployment-wide, read-only** | `GET /api/v1/org/*` (§3) |

New RBAC permission `org.report.read` (`09 §4`). It is **not** a rung on the per-workspace `RANK`
ladder (`viewer<builder<publisher<admin`) — a workspace `admin` is still only an admin *of that
workspace*. `org.report.read` is a **plane-level** permission, granted solely by `platform_admins`
membership. A finer `org.report.content_read` (§2) gates the one action that can see private content.

### 1.2 Bootstrap

The first `platform_admin` is **seeded at deploy** (env/`org.config.json` subject, written with
`granted_by = 'deploy'`, same deploy-time channel as branding, `13 §2`). Thereafter a `platform_admin`
may grant another — an **audited** action (`org.admin.grant` / `org.admin.revoke`, written to the
org-level audit sink, §2). This avoids a chicken-and-egg lockout without a side door into the DB.

## 2. The elevated, audited cross-tenant read path

This is the path `09 §7` and `01 §6` name but leave unspecified. Every other query in the system runs as
the non-owner app role with RLS forcing `workspace_id = current_setting('app.workspace_id')` (`01 §6`).
Governance needs **one** sanctioned way to read across all workspaces. Use a **`SECURITY DEFINER`
Postgres function** owned by the table-owning (migration) role — which is *not* subject to the RLS
policies — so it reads every workspace's rows **without** the per-request `SET LOCAL app.workspace_id`
(`09 §3`) that pins the tenant planes:

```sql
-- services/api/migrations/org_report_fns.sql
-- THE SINGLE sanctioned cross-tenant read. Owned by the DDL/migration role (the table owner), so it is
-- not constrained by the RLS policies (01 §6) that gate the non-owner app role. It is therefore kept
-- (a) read-only, (b) STABLE, (c) EXECUTE-locked to the elevated reader role only. A dedicated BYPASSRLS
-- reader role is an alternative mechanism; the SECURITY DEFINER function is narrower (one audited surface).
create or replace function org_report_agents()
  returns table (
    agent_id uuid, workspace_id uuid, owner_subject text, status agent_status,
    active_version_id uuid, drift drift_state, visibility text, registered_at timestamptz
  )
  language sql stable security definer set search_path = public
as $$
  select a.id, a.workspace_id, a.owner_subject, a.status, a.active_version_id, a.drift,
         a.access_policy->>'visibility',            -- policy tier; drives the metadata-only redaction below
         a.registered_at                            -- from 14 (P0-1); null = unregistered (§3 compliance)
  from agents a;                                     -- reads EVERY workspace — the one place RLS is crossed
$$;

revoke all on function org_report_agents() from public;
grant execute on function org_report_agents() to platform_report;   -- elevated reader role, used only by the §3 router
```

```python
# services/api/org/deps.py — the platform-admin plane dependency (parallel to get_workspace_ctx, 09 §3)
async def require_platform_admin(
    request: Request,
    principal: Principal = Depends(get_principal),   # the SAME OIDC session as the console plane (09 §2)
    db: AsyncSession = Depends(get_db),
) -> PlatformAdminCtx:
    # NO workspace selection, NO `SET LOCAL app.workspace_id`: this plane is deployment-wide, not tenant-scoped.
    if not await is_platform_admin(db, principal.subject_id):     # platform_admins roster (§1)
        raise Forbidden("requires platform_admin")                # → 403 access.denied (08 §1)
    return PlatformAdminCtx(principal=principal)
```

### 2.1 It is the single exception to the isolation suite

The tenant-isolation suite (`09 §8`) otherwise asserts **no cross-tenant read exists** (tenant A never
sees tenant B). This path is the **one** sanctioned crossing. The suite must therefore gain an explicit
test that **(a)** `org_report_*` / `/api/v1/org/*` is the *only* code path that returns rows from more
than one `workspace_id`, and **(b)** every crossing writes an audit row (§2.2). Any *other* path that
crosses is a bug the suite must still catch.

### 2.2 Every call is audited at the org level

Each `org.report.read` call writes an audit row with the **actor** (the platform admin's subject). The
sink is `audit_logs` (`01 §4.8`), but org-level actions are not owned by any workspace, so they are
written with **`workspace_id = NULL`**:

```python
# services/api/org/audit.py
async def write_org_audit(db, *, actor: str, action: str, target: str, meta: dict | None = None):
    # workspace_id NULL = an ORG-LEVEL action, not a tenant action. This is a deliberate RLS carve-out:
    # the per-workspace audit surface (09 §12) must NEVER return these rows (its policy filters by
    # workspace_id = current_setting(...), which excludes NULL). They are readable only via this plane.
    await db.execute(
        text("insert into audit_logs (workspace_id, actor, action, target, meta) "
             "values (NULL, :actor, :action, :target, :meta)"),
        {"actor": actor, "action": action, "target": target, "meta": Json(meta or {})},
    )
```

This extends the audit sink defined in `01 §4.8` / `09 §12` in two additive ways (expand/contract,
`01 §7`): `audit_logs.workspace_id` becomes **nullable** (today it is `not null`), and the RLS policy on
`audit_logs` gains a carve-out so `NULL`-workspace rows are invisible to the tenant planes and surfaced
only through this path. New audit actions: `org.report.read`, `org.report.content_read`,
`org.admin.{grant,revoke}`.

### 2.3 Metadata-only for `private` resources (the hard constraint)

`access-policy.ts` defines a **`private`** tier: owner-only, Splunk-style — only the resource owner
(plus a *workspace* admin) may use or manage it. A platform admin is **not** a workspace admin and holds
**no owner subject**, so the org report must honor the private tier. The rule:

- For a `private` agent the report MAY show **metadata**: existence, owning workspace, owner, status,
  active-version label, eval verdict, drift, 30-day spend, policy tier, compliance flags.
- It MUST NOT show **content**: the `system_prompt`, version snapshot internals, run inputs/outputs,
  traces, or eval-case I/O.
- Any content access on a private resource is a **separate, explicitly-audited** action
  (`org.report.content_read`, gated by the finer permission in §1.1) — never a side effect of the estate
  report. (`09 §7`'s "separate explicitly-audited path" phrasing is honored at *two* levels: the
  cross-tenant read itself, and the content-vs-metadata split within it.)

This is what reconciles org oversight with the private tier: leadership can answer "does an unevaluated,
over-budget, unowned agent exist in `trellix-grc`?" **without** the platform-admin role becoming a
read-anything backdoor that defeats `private`. Oversight sees *that* it exists and *whether* it is
compliant; it does not get to read *what it says*.

```python
# services/api/org/project.py — enforce the metadata/content split at projection time
def project_estate_row(r) -> dict:
    row = {  # metadata — always allowed, even for private
        "agent": r.agent_id, "workspace": r.workspace_id, "owner": r.owner_subject,
        "status": r.status, "activeVersion": r.active_version_label, "evalVerdict": r.eval_verdict,
        "drift": r.drift, "spend30d": r.spend_30d, "policyTier": r.visibility,
        "complianceFlags": r.compliance_flags,
    }
    # NOTE: system_prompt / outputs / case I/O are never assembled here. Content for a private resource
    # is a separate endpoint + separate audit action (org.report.content_read), out of this projection.
    return row
```

## 3. Report endpoints

**Query-time aggregation, no new rollup table in P1.** Reuse the `08 §7` dashboard pattern: aggregate in
SQL with `date_trunc` / `sum`, cache per `(metric, range)` in Redis with a short TTL (org views tolerate
seconds of staleness). The **scale path** (if the estate grows past what a synchronous cross-tenant scan
can serve) is a **nightly materialized view** refreshed by the worker — called out as a known lever, not
built in P1. Aggregation spans `agents`, `agent_versions` (status / `publishable`), `eval_runs`
(verdict / quality / drift), `runs` (`cost_usd`), `spend_limits`, and `audit_logs` (`01`).

All three endpoints are **auth `P`** (platform-admin, §1), run through the **§2 elevated path**, carry
**no `?workspace=`** selection (deployment-wide by definition), and are **audited** on every call.

| Method · Path | Auth | Async | Returns |
|---|---|---|---|
| `GET /api/v1/org/agents` | **P** | — | the estate: one row per agent across all workspaces |
| `GET /api/v1/org/spend` | **P** | — | spend rollup by workspace / agent / model |
| `GET /api/v1/org/compliance` | **P** | — | the non-compliant list (the exceptions report) |

### 3.1 `GET /api/v1/org/agents` — the estate

One row per agent, every workspace:
`{ agent, workspace, owner, status, activeVersion, evalVerdict, drift, spend30d, policyTier,
complianceFlags[] }`. `policyTier` is `access_policy.visibility` (`workspace|restricted|private`);
private rows are metadata-only (§2.3).

```python
# services/api/org/routes.py
@router.get("/api/v1/org/agents")
async def org_agents(ctx: PlatformAdminCtx = Depends(require_platform_admin), db=Depends(get_db)):
    rows = await db.execute(text("select * from org_report_agents()"))     # the ONE cross-tenant read (§2)
    estate = await enrich_estate(db, rows)   # joins active version, latest eval_run verdict, spend_30d, flags
    await write_org_audit(db, actor=ctx.principal.subject_id,
                          action="org.report.read", target="org/agents")   # workspace_id NULL (§2.2)
    return [project_estate_row(r) for r in estate]                         # metadata-only for private (§2.3)
```

### 3.2 `GET /api/v1/org/spend` — rollup by workspace / agent / model

Same cost figure the per-workspace dashboards and the quota engine use — `runs.cost_usd` computed from
capability profiles (`02`, `07`, `10 §6`) — now un-partitioned and grouped three ways:

```sql
-- behind GET /api/v1/org/spend; called only inside a SECURITY DEFINER report fn (§2), range default 30d
select r.workspace_id, r.agent_id, (s.attrs->>'model') as model, sum(r.cost_usd) as usd, count(*) as runs
from runs r
left join spans s on s.run_id = r.id and s.kind = 'model'
where r.created_at >= now() - interval '30 days'
group by rollup (r.workspace_id, r.agent_id, model);     -- workspace / agent / model subtotals in one scan
```

Each workspace line can be flagged against its `spend_limits.monthly_budget_usd` (`01 §4.8`) to surface
over-budget tenants here as well as in the compliance report.

### 3.3 `GET /api/v1/org/compliance` — the exceptions report

The non-compliant list — the report leadership actually acts on. Four flags:

| Flag | Rule | Source |
|---|---|---|
| `unregistered` | `agents.registered_at IS NULL` | `14` (P0-1) |
| `unevaluated` | no `eval_run` with `verdict = 'pass'` for the active version | `eval_runs` (`01 §4.5`) |
| `over_budget` | workspace 30-day spend ≥ `spend_limits.monthly_budget_usd` | `runs` + `spend_limits` (`01`) |
| `no_owner` | `agents.owner_subject` empty / unresolvable | `agents` (`01 §4.2`, `14`) |

`unregistered` and `no_owner` **consume the P0-1 registration fields** (`agents.registered_at`,
`owner_subject`) introduced in `14` — this report is the primary *reader* of that registration data, the
reason it is captured. Keeping compliance query-time (not a stored status) means a newly-registered or
freshly-evaluated agent drops off the list on the next read, with no reconciliation job to drift out of
sync.

```python
@router.get("/api/v1/org/compliance")
async def org_compliance(ctx: PlatformAdminCtx = Depends(require_platform_admin), db=Depends(get_db)):
    flagged = await db.execute(text("select * from org_report_compliance()"))   # cross-tenant, SECURITY DEFINER (§2)
    await write_org_audit(db, actor=ctx.principal.subject_id,
                          action="org.report.read", target="org/compliance")    # audited (§2.2)
    return [project_compliance_row(r) for r in flagged]                         # metadata-only for private (§2.3)
```

→ Next: `16-notifications.md`

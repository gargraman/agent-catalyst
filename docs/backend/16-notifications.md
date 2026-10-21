# 16 — Owner Notifications (closing the "drift detected, nobody told" gap)

> Implements the owner-facing half of `spec.md A2/B5` and `scope.md`'s founding problem. The platform's
> whole reason to exist is "when the model changes, **nobody notices** the agent silently got worse"
> (`scope.md`, `05-eval-drift.md`). Today every alerting path is **operator-facing** (`10 §5`
> Alertmanager → PagerDuty), and four signals that bear directly on an agent's **owner** — it drifted,
> its eval failed, it crossed a budget threshold, its pinned model was deprecated — are each *computed*
> and then **tell nobody**. A drift detector whose verdict reaches no human restates the exact problem
> the product claims to solve. This doc specifies the in-product **notifications** surface that closes
> that loop. Cross-refs: `05` (drift/eval), `07` (budget), `02` (capability profiles), `10` (ops alerts
> vs owner notifications), `01` (DDL/RLS), `08` (routes), `14` (owner registration — a hard dependency).

## 1. The `notifications` entity

A tenant-owned, append-mostly table: one row per thing an owner should know about. `WorkspaceScoped`
(01 §5) + RLS (01 §6) like every other tenant table. The **canonical DDL home is `01 §4.8`** (added
alongside `jobs` and `audit_logs`); it is reproduced here so this doc is self-contained — if the two
ever disagree, **01 wins**.

```sql
-- canonical in 01 §4.8; shown here for reference. WorkspaceScoped + RLS (01 §5/§6).
create type notification_kind as enum (
  'drift',              -- a version's drift state transitioned for the worse (05 §5)
  'budget_threshold',   -- workspace spend crossed 80% / 90% of cap (07 §3)
  'model_deprecated',   -- a published version's pinned model went deprecated/retired (§3)
  'eval_failed',        -- an eval run finished with verdict=fail (05 §7)
  'approval_requested', -- a requires_approval tool call paused a run, awaiting a decision (§2.6, 09 §10)
  'registration_gate',  -- owner registration / routability gate (14) — reserved
  'dedup_flag'          -- duplicate-signal collapse surfaced to the owner — reserved
);

create table notifications (
  id                uuid primary key default gen_random_uuid(),
  workspace_id      uuid not null references workspaces(id),
  agent_id          uuid references agents(id),          -- NULL for workspace-level (e.g. budget_threshold)
  recipient_subject text not null,                       -- OIDC subject the row is addressed to
  kind              notification_kind not null,
  severity          text not null,                       -- info | warning | critical
  title             text not null,                       -- short, human-readable headline
  payload           jsonb not null default '{}',         -- kind-specific detail (deltas, model id, thresholds, deep-link ids)
  dedupe_key        text not null,                       -- hash(agent_id, kind, trigger) — see §2 (always set)
  created_at        timestamptz not null default now(),
  read_at           timestamptz                          -- NULL = unread (drives the unread count)
);

-- inbox + unread count for one principal; feed ordering; duplicate-collapse.
create index on notifications (workspace_id, recipient_subject, read_at);
create index on notifications (workspace_id, created_at desc);
-- Collapse repeated OPEN notifications of the same (agent, kind, trigger) — but PER RECIPIENT: one event
-- fans out to N recipients (§1.1) as N rows sharing a dedupe_key, so recipient_subject MUST be in the key,
-- else only the first recipient is ever notified. Canonical DDL: 01 §4.9.
create unique index notifications_open_dedupe
  on notifications (workspace_id, recipient_subject, dedupe_key)
  where read_at is null;
```

**Severity is independent of kind** — a `drift` into `watch` is `warning`, into `regressed` is
`critical`; an `eval_failed` is `critical`; a `budget_threshold` at 80% is `warning`, at 90%
`critical`. The emitter sets it (§2); the FE renders tone from it (§4).

### 1.1 Recipient resolution

Who gets a row is resolved at **emit time**, not read time:

- **The agent's owner** — `agents.owner_subject` (01 §4.2). This is a **hard dependency on `14`**: the
  registration gate must capture `owner_subject` as a **routable** OIDC subject (a real, addressable
  identity, not a placeholder) at agent creation. If owners aren't routable, the whole surface
  silently no-ops — the founding problem again. (`14` owns that gate; this doc assumes it.)
- **Plus workspace publishers + admins** — ⚠️ **re-sourced (2026-10-20 group-driven pivot):** there is no
  `memberships` roster anymore. The recipient set is now **platform-admins** (the `platform_admins` plane, 15)
  **+ the members of the workspace's backing Okta group when its `member_role = 'publisher'`** (the group
  confers one role to all). So an agent that outlives its original owner still has someone watching.

One logical event therefore fans out to **N rows, one per recipient subject** (same `dedupe_key`,
different `recipient_subject`). This keeps the read model trivial: "my inbox" is a single-table filter.

### 1.2 Tenancy: RLS scopes the workspace, the app scopes the recipient

RLS (01 §6) filters `notifications` to `app.workspace_id` — the backstop against cross-tenant leakage.
It does **not** know about `recipient_subject`. Within-workspace addressing is **app-level**: every
read route adds `where recipient_subject = :principal_subject` (the session principal, 08 §1). RLS =
tenant isolation; the app = "this row is addressed to *me*." Both must hold; neither alone is enough.

## 2. Emit points (the four signals that currently dead-end)

Each of these already *computes* the fact and then drops it. The change is one `emit_notification(...)`
call at the point of computation — no new detection logic (except §3). Emission is **best-effort and
out-of-band**: it never blocks or fails the eval/publish/invoke path that triggered it (a notification
write failing must not fail a publish).

```
      compute (already exists)                     emit (new)                   surface (§4)
  ┌────────────────────────────┐          ┌────────────────────────┐      ┌──────────────────┐
  │ 05 §5  drift-state change   │──────────▶                        │      │                  │
  │ 05 §7  eval run → fail      │──────────▶  emit_notification()    │─────▶│  topbar bell +   │
  │ 07 §3  BudgetGuard crossing │──────────▶  (dedupe → N rows)      │      │  inbox (owner)   │
  │ §3     model-deprec detector│──────────▶                        │      │                  │
  └────────────────────────────┘          └────────────────────────┘      └──────────────────┘
```

```python
# services/api/notifications.py  (thin; called from the emit points below)
async def emit_notification(
    *, workspace_id, agent_id, kind: str, severity: str, title: str,
    payload: dict, trigger: str,
) -> None:
    """Fan out one event to every recipient (§1.1) as best-effort rows. The partial-unique index
    (§1) means a repeated OPEN signal for the same (agent, kind, trigger) is a no-op, not a dupe."""
    dedupe_key = sha256(f"{agent_id}:{kind}:{trigger}")           # §2.5
    recipients = await resolve_recipients(workspace_id, agent_id) # owner + publishers/admins (§1.1)
    for subject in recipients:
        await insert_ignore_conflict(                            # ON CONFLICT DO NOTHING on the open-dedupe index
            workspace_id=workspace_id, agent_id=agent_id, recipient_subject=subject,
            kind=kind, severity=severity, title=title,
            payload=payload | {"dedupe_key": dedupe_key, "trigger": trigger},
            dedupe_key=dedupe_key,
        )
```

### 2.1 Drift — emit on *state transition*, not on every eval

`05 §5 on_version_change` flips `publishable`; the eval worker (05 §7) also computes the agent's
`drift_state` (`none | stable | watch | regressed`, 01 §3, `agents.drift`). Emit **only when that state
transitions for the worse** — `none→watch`, `watch→regressed`, `none→regressed`:

```python
# services/evals/worker.py (after writing eval_runs + setting agents.drift, 05 §7)
# Rank the states so we emit only on a WORSE transition. Membership in {watch,regressed} is not enough:
# an improving regressed→watch also enters that set and would spuriously alert "drifted to watch".
DRIFT_RANK = {"none": 0, "stable": 1, "watch": 2, "regressed": 3}
if DRIFT_RANK[new_drift] > DRIFT_RANK[old_drift] and new_drift in ("watch", "regressed"):
    await emit_notification(
        workspace_id=ws, agent_id=agent.id, kind="drift",
        severity="critical" if new_drift == "regressed" else "warning",
        title=f"{agent.name} drifted to {new_drift}",
        payload={"from": old_drift, "to": new_drift, "version": version.label,
                 "eval_run_id": run.id, "worst_dimensions": run.worst_dimensions},
        trigger=f"drift:{old_drift}->{new_drift}",
    )
```

Emitting on *every* eval (not just transitions) is precisely the noise that gets owners to tune the
whole surface out — so a `watch` agent re-evaluated five times produces **one** open notification, not
five (the dedupe index + transition guard both enforce this).

### 2.2 Eval failed

`05 §7` writes the terminal `eval_runs` row. On `verdict == "fail"`, emit `eval_failed` (`critical`)
with the run's `reason` and `failing_cases` ids so the inbox can deep-link to the eval detail. A
`low_confidence` verdict is **not** an owner notification — it's a publish-gate condition (08 §6), not
a regression the owner must act on.

> **A skill-content change needs NO new `notification_kind`.** Editing or re-publishing a bound skill is a
> `DRIFT_TRIGGER` (05 §5) that flips `publishable=false` and demands a fresh eval on every bound agent — so it
> reuses **exactly** the existing re-eval → `drift` / `eval_failed` emit points above (§2.1, §2.2). The owner is
> told through the same path as a prompt or KB change; no skill-specific kind, payload, or detector is added.

### 2.3 Budget threshold

`07 §3 BudgetGuard.reconcile` is where running spend is known. When monthly spend crosses **80%** or
**90%** of the workspace cap (`spend_limits`, 01 §4.8), emit `budget_threshold` (`agent_id = NULL`;
this is workspace-level). This is distinct from the hard-cap `402 budget.exceeded` (08 §1) and from the
ops `SpendAnomaly` page (10 §5): those stop/alert *the system*; this tells the *owner* "you're about to
run out," before enforcement bites.

```python
# services/runtime/cache/budget.py (inside reconcile, 07 §3) — fire once per threshold per month
for pct in (0.90, 0.80):
    if crossed(before, after, cap, pct):                         # before < pct*cap <= after
        await emit_notification(
            workspace_id=ws, agent_id=None, kind="budget_threshold",
            severity="critical" if pct >= 0.90 else "warning",
            title=f"Workspace at {int(pct*100)}% of monthly budget",
            payload={"pct": pct, "spent_usd": after, "cap_usd": cap},
            trigger=f"budget:{int(pct*100)}:{month}",            # month in the key → one per month per level
        )
        break
```

### 2.4 Model deprecated

Emitted by the new detector worker in §3 (`model_deprecated`, `critical`) to the owner of each
published version whose pinned model has gone `deprecated`/`retired`.

### 2.5 Dedupe is the anti-noise mechanism

Every emit computes `dedupe_key = hash(agent_id, kind, trigger)`. Combined with the **partial-unique
index on open rows** (§1), repeated signals for the same cause collapse into the single already-unread
row — they don't pile up. Marking a notification read releases the key, so a *recurrence after
acknowledgement* can open a fresh one. The `trigger` string is what distinguishes "the same ongoing
condition" from "it happened again": include the discriminator that matters (the drift transition, the
budget level + month, the model id) and nothing that merely changes every run (timestamps, run ids).
Noise → ignored → "nobody noticed" is the failure mode this exists to prevent.

### 2.6 Approval requested (destructive-tool human-approval gate)

The one emit point whose recipients are **not** the §1.1 owner set. When `runtime`'s `tool_approval_gate`
node (03) hits a tool binding with `requires_approval` (a `tool_risk_tier` the policy marks destructive —
01, 09 §10), it **pauses the run** and writes a `tool_approvals` row (`approval_status='pending'`, 01); the
run resumes only on an approve/deny decision (`POST /v1/approvals/:id/decide`, 08). That pause must reach
whoever can *act on* it, so this kind addresses **the workspace's publishers + admins** — the members of
the workspace's backing Okta group when its `member_role='publisher'`, plus `platform_admins` (the
group-driven model, §1.1; there is no `memberships` roster) — **not** `agents.owner_subject`. A
builder can author a destructive binding but, by the reconciled RBAC (09, spec B12), approving its live
execution is a publisher/admin decision.

```python
# services/runtime/graph/nodes/tool_approval_gate.py (after writing the pending tool_approvals row, 03)
await emit_notification(
    workspace_id=ws, agent_id=agent.id, kind="approval_requested", severity="warning",
    title=f"{agent.name}: approval needed to run {tool_name}",
    payload={"approval_id": approval.id, "tool": tool_name, "risk_tier": risk_tier,
             "run_id": ctx.trace_id, "args_preview": redacted_args},
    trigger=f"approval:{approval.id}",                 # one row per pending approval; read on decide
    recipients="publishers_admins",                   # OVERRIDE of the §1.1 owner+pub/admin default
)
```

This reuses the **same dedupe/fan-out machinery** (§2 `emit_notification`): one pending approval fans out
to N publisher/admin rows sharing `dedupe_key = hash(agent_id, 'approval_requested', approval:<id>)`, so
every approver sees it once and a repeated pause for the *same* approval collapses into the single open
row. Deciding the approval (08) marks the rows read (releases the key). The matching `notification_kind`
enum value is added in **01 §3** (table DDL in 01 §4.8); recipient resolution for this kind returns
publishers+admins only (an additive branch in `resolve_recipients`, §1.1).

## 3. Model-deprecation detector worker

The one genuinely *new* detector. It answers: "a model an owner pinned months ago is being sunset —
does the owner know?" Nothing in the system tells them today.

### 3.1 Hard dependency: a profile availability field (new, in `02`/`01`)

`02 §4 CapabilityProfile` currently has **pricing + limits + features only — no availability/lifecycle
field**. This detector cannot exist until one is added:

```jsonc
// addition to services/runtime/providers/profiles/*.json (02 §4) — NEW fields
{ "availability": "active",       // active | deprecated | retired
  "sunset_at": "2026-12-31" }     // optional ISO date when deprecated/retired takes effect
```

```python
# CapabilityProfile (02 §4) gains two fields — cross-ref 02 as the owner of this change:
availability: str = "active"      # active | deprecated | retired
sunset_at: str | None = None
```

Because a resolved profile is **snapshotted into each version** (`agent_versions.profile_snapshot`, 01
§4.2), the detector compares the *live* profile's `availability` against the model each published
version actually pins — not against the (frozen) snapshot, which by design never changes.

### 3.2 The worker

```python
# services/evals/model_deprecation.py — periodic job (job_kind extension; scheduled like other workers, 10 §9)
async def detect_deprecated_models():
    for pe in published_endpoints():                             # only PUBLISHED versions matter (01 §4.7)
        version = snapshot_of(pe.pinned_version_id)
        model = version.profile_snapshot["model"]               # the model the owner pinned
        prof = resolve_profile(version.provider, model, version.api_version)  # LIVE profile (02 §4)
        if prof.availability in ("deprecated", "retired"):
            await emit_notification(
                workspace_id=pe.workspace_id, agent_id=pe.agent_id, kind="model_deprecated",
                severity="critical",
                title=f"{model} is {prof.availability} — {pe.agent_slug} must migrate",
                payload={"model": model, "availability": prof.availability,
                         "sunset_at": prof.sunset_at, "version": version.label},
                trigger=f"model_deprecated:{model}:{prof.availability}",  # one per model per state
            )
```

### 3.3 Two honest limits

- **It only fires if capability profiles are kept current.** Profiles are hand-maintained data files
  (02 §4); if nobody marks a model `deprecated` when the provider announces it, the detector stays
  silent. The detector is only as good as the **profile-currency maintenance process** — state that
  plainly rather than imply automatic coverage.
- **This is the notification hook, not the migration.** It is the trigger surface for the **P1-6
  forced-migration** work (guided re-point to a supported model, with a fresh eval). The actual
  migration workflow — picking a replacement, re-evaluating, re-pointing — is **out of scope here**;
  this doc only guarantees the owner is *told*.

## 4. Delivery + surface

### 4.1 Routes (console session, auth **S** — 08 §1)

These belong in the `08` catalog (another editor adds them there); specified here so the contract is
pinned:

| Method · Path | Auth | Purpose |
|---|---|---|
| `GET /api/v1/notifications?workspace=:id` | S | list this principal's notifications + `unread_count` (filtered `recipient_subject = principal`, §1.2) |
| `POST /api/v1/notifications/:id/read` | S | mark one read (sets `read_at`, releases the dedupe key §2.5) |
| `POST /api/v1/notifications/read-all` | S | mark all this principal's unread rows read |

```python
# services/api/schemas/notifications.py — mirrors the FE row the inbox renders (§4.3)
class NotificationOut(BaseModel):
    id: str
    kind: str                         # notification_kind (01 §3)
    severity: str                     # info | warning | critical
    title: str
    payload: dict = {}
    agent_id: str | None = Field(default=None, alias="agentId")
    created_at: datetime = Field(alias="createdAt")
    read_at: datetime | None = Field(default=None, alias="readAt")
    class Config: populate_by_name = True

class NotificationList(BaseModel):
    items: list[NotificationOut]
    unread_count: int = Field(alias="unreadCount")
    class Config: populate_by_name = True
```

### 4.2 Owner notifications are NOT ops alerts

A hard line, because conflating the two is how owners get paged for infra and operators get spammed
with per-agent drift:

| | **Owner notifications (this doc)** | **Ops alerts (10 §5)** |
|---|---|---|
| Audience | the agent's owner + ws publishers/admins | on-call operators |
| Channel | **in-product** (bell + inbox) | Alertmanager → page/Slack |
| Examples | drift, eval fail, budget 80/90%, model deprecated | latency p99, breaker open, readiness, spend anomaly |
| Store | `notifications` (Postgres, tenant-scoped) | Prometheus / Alertmanager |
| Lifecycle | read/unread per recipient | fires/resolves on SLI |

Spend has an entry on *both* sides on purpose: the **owner** gets "80% of budget" (act on it);
**operators** get the "3× week-ago" anomaly page (07 / 10 §5). Same money, two audiences, two surfaces.

### 4.3 Frontend contract (described, not built here)

The console has **no notifications UI today** — `app-shell.tsx`'s topbar is a search box only
(`components/app-shell.tsx`, the `<header>` at the sticky topbar; no bell). The intended integration,
reusing primitives that already exist:

- **Topbar bell + inbox dropdown** in `app-shell.tsx`, badged with `unread_count`; clicking an item
  deep-links to the agent's eval / settings / dashboard via `agent_id` + `payload`.
- **Reuse the `Toast` primitive** (`store.ts` — `Toast`/`toast()`) for a transient pop when a
  `critical` notification arrives while the console is open; the inbox is the durable record, the toast
  is the nudge. Tone maps from `severity` (`critical`→critical, `warning`→neutral/warning).
- **Reuse the `Activity` shape** (`data.ts` — `Activity = {who, action, target, time, tone}` +
  `activityFor`): the inbox row renders almost 1:1 against it (the dashboard activity feed, 08 §7, is
  the audit-derived cousin of this owner-addressed feed). `severity → tone`, `title → action/target`.

This FE wiring is an **integration-map / `@backend` follow-up** (per `docs/backend-integration-map.md`
conventions and the project's `@backend` markers) — the mock console gains a bell backed by the three
routes above; it is **not implemented in this design doc**.

→ Next: back to the [README](README.md) index — this is the last design doc; notifications close the
loop the eval/drift core (05) opened, so "drift detected" can no longer mean "nobody told."

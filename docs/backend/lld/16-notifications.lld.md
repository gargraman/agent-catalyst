# 16 (LLD) — Owner Notifications: delivery, preferences, reliable fan-out

> **Parent:** [`../16-notifications.md`](../16-notifications.md) (the `notifications` entity, recipient
> resolution, the four emit points + the model-deprecation detector, dedupe, the in-product routes).
> The parent builds the **in-product** surface end-to-end: the Postgres row is written at emit time, the bell +
> inbox read it, dedupe collapses noise. It explicitly scopes delivery to **in-app only** (`16 §4.2`:
> "Channel: in-product"). This LLD specifies the **delivery layer** that was left for later: the
> notification-model split (record vs delivery), per-user/per-workspace **preferences**, **templating**, and the
> worker-side **reliable fan-out** to external channels (email / Slack / webhook) with retry + dead-letter — all
> as an **additive extension** over the in-app record, never a replacement for it.

## What this adds over parent `16`

| Gap in `16` | Filled here |
|---|---|
| `16 §4.2` fixes **in-app** as the channel; email/Slack/webhook are not designed | §1 — the record-vs-delivery split; `notification_deliveries`; in-app = system of record, external = opt-in fan-out |
| No preference model — who wants what, on which channel | §2 — per-user × per-kind × per-channel preferences + workspace defaults + the precedence rule |
| `16 §1`/`§2` payloads are kind-specific blobs; no rendering to human text per channel | §3 — the per-kind template registry + channel renderers |
| `16 §2` emit is "best-effort out-of-band"; external delivery needs **queued, retried, dead-lettered** sending — undefined | §4 — the arq delivery job, retry/backoff, DLQ, idempotency tie-in to `00-conv §3` |
| Edge cases named loosely (noise) but not: unverified address, provider rate-limit, digest batching | §5 — the delivery-specific error/edge taxonomy |
| `12-channels` (inbound invoke) vs outbound platform messages easily confused | §6 — the one-paragraph boundary |

Python 3.12, `from __future__ import annotations`, `AsyncSession` (asyncpg), arq on the worker
([`00-conv §4`](00-conventions.lld.md), `11 §1`). `emit_notification`, the `notifications` DDL,
`resolve_recipients`, the emit points: **see `16` — not restated.** Snippets compile (§7).

---

## 1. The model: in-app record vs external delivery

The parent's `notifications` row (`16 §1`, canonical DDL `01 §4.8`) is the **system of record** and the
**in-app channel simultaneously** — writing the row *is* delivering the in-app notification (the bell reads it,
`16 §4`). External channels are a **separate, best-effort fan-out** that reads that same row. Keeping them split
is the design rule: a failed Slack send must never lose the durable in-app record.

```sql
-- NEW (canonical home: 01 §4.9, alongside the notifications DDL). One row per (notification, external channel)
-- attempt-group. In-app is NOT tracked here — the notifications row IS the in-app delivery.
create type delivery_channel as enum ('email','slack','webhook');     -- in_app is implicit (the row itself)
create type delivery_status  as enum ('queued','sent','failed','dead','skipped');

create table notification_deliveries (
  id              uuid primary key default gen_random_uuid(),
  workspace_id    uuid not null references workspaces(id),             -- WorkspaceScoped + RLS (01 §5/§6)
  notification_id uuid not null references notifications(id) on delete cascade,
  channel         delivery_channel not null,
  target          text not null,                                      -- email addr / slack user-or-channel / webhook url
  status          delivery_status not null default 'queued',
  attempts        int not null default 0,
  last_error      text,
  dedupe_key      text not null,                                      -- = notifications.dedupe_key + channel (§4)
  created_at      timestamptz not null default now(),
  sent_at         timestamptz
);
create unique index notification_deliveries_idem
  on notification_deliveries (workspace_id, notification_id, channel);  -- one delivery per channel per notification
create index on notification_deliveries (status, created_at);           -- the DLQ / retry sweep
```

**Flow:** `emit_notification` (`16 §2`) writes the `notifications` row(s) as today (in-app done). Then — *still
best-effort, out-of-band* — it resolves each recipient's enabled external channels (§2) and enqueues one arq
delivery job per `(notification, channel)` (§4). The external send never runs inline on the eval/publish/invoke
path (`16 §2`).

```python
# services/api/notifications.py — the ADDITIVE tail of emit_notification (16 §2). In-app write unchanged above.
async def fan_out_external(db, redis, notification_row) -> None:
    """Best-effort: enqueue external deliveries for one persisted notification. A failure here never
    propagates — the in-app row already exists (the durable record)."""
    prefs = await load_prefs(db, notification_row.recipient_subject, notification_row.workspace_id)  # §2
    for channel, target in enabled_targets(prefs, notification_row.kind):        # §2 precedence
        row = await upsert_delivery(                                             # idempotent (unique index)
            db, notification_id=notification_row.id, channel=channel, target=target,
            dedupe_key=f"{notification_row.dedupe_key}:{channel}",
        )
        if row.status == "queued":
            await redis.enqueue_job(                                             # worker §4
                "deliver_notification", notification_row.workspace_id, str(row.id), _queue="queue:batch",
            )
```

---

## 2. Preferences & precedence

Who receives *which kinds* on *which channels*. Two levels — a **workspace default** an admin sets, and a
**per-user override** — plus a hard floor so the founding problem (`16` intro: "nobody told") can't re-open.

```sql
-- canonical: 01 §4.9. Per-user overrides; NULL subject = the workspace default row.
create table notification_prefs (
  workspace_id uuid not null references workspaces(id),
  subject      text,                                   -- OIDC subject; NULL = workspace default
  kind         notification_kind not null,             -- 01 §3; one row per kind (or '*' sentinel default)
  in_app       boolean not null default true,
  email        boolean not null default false,
  slack        boolean not null default false,
  webhook      boolean not null default false,
  digest       boolean not null default false,         -- batch non-critical into a periodic digest (§5)
  primary key (workspace_id, subject, kind)
);
```

**Precedence (first match wins):**

1. **per-user row** for `(subject, kind)` → use it.
2. else **per-user `'*'`** default row → use it.
3. else **workspace default** (`subject IS NULL`) for the kind → use it.
4. else **built-in floor:** `in_app = true`, all external `false`.

**Non-negotiable floor (independent of prefs):** `in_app` is **always on** for the agent's owner
(`agents.owner_subject`, `16 §1.1`) and cannot be disabled — the in-app row is the record. A user may silence
*external* channels and *non-owner* in-app rows, never their own owner in-app feed. A `critical` severity
(`16 §1`: `drift→regressed`, `eval_failed`, `model_deprecated`, budget 90%) **ignores `digest`** and delivers
immediately on every enabled channel (§5).

`enabled_targets(prefs, kind)` returns the external `(channel, target)` pairs whose pref flag is true, resolving
`target` from identity: email from the OIDC profile (`09`), Slack from the `idp_binding`/channel link (`12`/`18`),
webhook from a workspace-configured URL. A channel with **no resolvable target** is `skipped` (§5), never
`failed`.

---

## 3. Templating

The parent's `payload` (`16 §1`) is machine detail; each channel needs human text. A **per-kind template
registry** renders `(title, payload)` → a channel-appropriate body. Templates are code (versioned with the repo),
not tenant data — there is no per-workspace template editing (out of scope; `scope.md` "plain software" tier).

```python
# services/worker/notifications/templates.py — one entry per notification_kind (01 §3)
from __future__ import annotations

def render(kind: str, channel: str, title: str, payload: dict) -> dict:
    """Return the channel-shaped body. Email → {subject, html, text}; slack → {blocks}; webhook → the raw
    JSON envelope (payload passed through + title). Falls back to a generic text template for an unknown kind
    so a newly-added kind never crashes delivery — it just renders plainly."""
    tmpl = _REGISTRY.get(kind, _GENERIC)
    body = tmpl[channel](title, payload) if channel in tmpl else _generic_for(channel, title, payload)
    return body

# example: the drift kind (16 §2.1 payload: from/to/version/eval_run_id/worst_dimensions)
def _drift_email(title: str, p: dict) -> dict:
    return {
        "subject": title,                                         # "{agent} drifted to regressed"
        "text": f"{title}\nfrom {p['from']} → {p['to']} on version {p['version']}.\n"
                f"Worst dimensions: {', '.join(p.get('worst_dimensions', []))}.\n"
                f"Open the eval: {_deeplink('eval', p.get('eval_run_id'))}",
        "html": None,                                             # HTML optional; text is the floor
    }
```

- **Deep links** are built from `agent_id` + `payload` ids (`16 §4.3`) to the console route — the same targets
  the in-app inbox uses, so a Slack click and a bell click land in the same place.
- **Webhook** is the thinnest renderer: it ships the structured envelope (`kind`, `severity`, `title`, `payload`,
  `notification_id`) so an external system does its own formatting. This reuses the `10 §9 P2` "webhook on
  completion" idea but for notifications, not run completion — distinct trigger, same transport shape.

**New kind — `shadow_usage` (owner nudge; `14` LLD §5).** Adoption `monitor` mode emits a `shadow_usage` owner
notification when a workspace shows a sustained direct-to-provider bypass signal
(`ac_shadow_provider_calls_total`, `10` LLD §6). It is `severity = warning` (an owner nudge, not a `critical`
block), so it honors `digest` batching (§5) and is deduped per `16 §2.5` so a chatty workspace nudges once per
window, not per denied call. It renders through the §3 registry like any kind (falling back to `_GENERIC` until
a bespoke template is added). It is distinct from the ops `SpendAnomaly`/egress alert (`10 §5`, the `10 §8`
"ops alert ≠ owner notification" split applies). The `notification_kind` enum (canonical `01 §3`) gains
`shadow_usage`.

---

## 4. Reliable delivery (worker, retry, dead-letter)

External sends run on the **worker batch pool** (`11 §1`), one arq job per `notification_deliveries` row.

```python
# services/worker/notifications/deliver.py — arq job (registered in WorkerSettings, 11 §1)
from __future__ import annotations

MAX_ATTEMPTS = 5

async def deliver_notification(ctx, workspace_id: str, delivery_id: str) -> None:
    # workspace_id is a job arg (enqueued in §1) so no cross-tenant read is needed to scope the session.
    async with SessionLocal() as session, job_tenant(session, workspace_id) as db:   # 09 §1.2 — RLS, SET LOCAL
        row = await db.get(NotificationDelivery, delivery_id)
        if row is None or row.status in ("sent", "dead", "skipped"):
            return                                                           # idempotent: already terminal
        notif = await db.get(Notification, row.notification_id)
        body = render(notif.kind, row.channel, notif.title, notif.payload)   # §3
        try:
            await _send(row.channel, row.target, body)                       # email/slack/webhook transport
            row.status, row.sent_at = "sent", now()
        except TransientDeliveryError as e:                                  # 429 / 5xx / timeout (cf. 02 §3)
            row.attempts += 1
            row.last_error = str(e)
            if row.attempts >= MAX_ATTEMPTS:
                row.status = "dead"                                          # → DLQ sweep (below)
            else:
                raise                                                        # arq retries with its own backoff
        except PermanentDeliveryError as e:                                  # unverified addr / bad webhook (§5)
            row.status, row.last_error = "failed", str(e)
        await db.commit()
```

- **Idempotency / dedup.** Two layers: (1) the `notification_deliveries` unique index on
  `(workspace_id, notification_id, channel)` means re-enqueueing the same delivery is a no-op upsert — the
  **send happens once per channel per notification**; (2) the job is self-idempotent (terminal-status early
  return), so an arq redelivery after a crash doesn't double-send. This is the delivery-layer analogue of the
  `00-conv §3` invoke idempotency rule — same principle (reserve-once, replay-safe), different store.
- **Retry** is arq's native retry (re-raise on transient) with capped exponential backoff, bounded by
  `MAX_ATTEMPTS`. Transient vs permanent classification mirrors `02 §3` (429/5xx/timeout = transient; 4xx auth /
  invalid target = permanent).
- **Dead-letter.** After `MAX_ATTEMPTS` a row is `dead`; a periodic sweep (same scheduler as the `16 §3`
  detector) surfaces `dead` counts to ops metrics (`10`) — a persistently-dead channel is an ops signal, not an
  owner notification (which would recurse).
- **Per-tenant scoping.** The job runs inside `job_tenant` (`09 §1.2`) so every read/write is RLS-scoped to the
  notification's workspace — a delivery job can never read another tenant's rows. The in-app row and its
  deliveries share `workspace_id`.
- **Best-effort contract preserved.** The whole external path is downstream of the persisted `notifications`
  row; if the worker/Redis is down, the in-app notification still exists and shows in the inbox on next load.

---

## 5. Delivery-specific error & edge taxonomy

Beyond the general taxonomy (`00-conv §1`) and the parent's dedupe (`16 §2.5`):

| Edge case | Handling |
|---|---|
| **Unverified / unresolvable address** | `PermanentDeliveryError` → delivery `failed` (not retried); in-app row unaffected. Email requires a verified address; an unverified one is `skipped` at enqueue (§2), so it never becomes a `failed` row. |
| **No resolvable target** (no Slack link, no webhook URL) | `skipped` at `enabled_targets` time — pref is on but channel isn't configured. Not an error. |
| **Provider rate-limit (429)** | `TransientDeliveryError` → arq backoff + `MAX_ATTEMPTS`; honor `Retry-After` like `02 §4`. A workspace-level token bucket caps external sends/min so a notification storm can't get the platform's Slack/email app throttled or banned. |
| **Digest batching** | `digest=true` prefs (§2) accumulate **non-critical** (`info`/`warning`) notifications; a scheduled digest job renders one rollup per user per period (reuses the `16 §3` scheduler). `critical` **bypasses digest** and sends immediately. This is the anti-noise counterpart to the in-app dedupe (`16 §2.5`): dedupe collapses same-cause repeats; digest batches distinct low-severity events. |
| **Notification storm** (many agents drift at once) | in-app dedupe already collapses per-cause (`16 §2.5`); external fan-out additionally respects the per-workspace rate cap + digest, so N simultaneous drifts don't fire N emails instantly. |
| **Delivery backend down** | in-app record intact (§1/§4); external retried then dead-lettered → ops metric. Owners still see everything in the inbox. |

---

## 6. Relationship to `12-channels` (do not confuse)

Two opposite directions, one word apart:

- **`12-channels` = inbound.** A messaging channel (Slack/Teams/Chat) is the **fourth caller of
  `runtime.invoke`** (`12 §1`, memory: "channel is the 4th caller"): a user message *comes in*, the agent runs,
  a reply *goes out as that agent's answer*. It is an **invoke path**.
- **`16` notifications = outbound platform messages.** The *platform* tells an owner "your agent drifted / failed
  eval / hit budget / pinned a deprecated model." No `invoke`, no agent, no model — plain fan-out of a computed
  fact.

They may share a transport (both can post to Slack) but never share a code path: a `12` reply is authored by an
agent run; a `16` Slack message is rendered by §3 from a `notifications` row. Keeping `delivery_channel` (§1)
distinct from the `channels` registry (`12`/`01`) in the schema enforces the separation.

---

## 7. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as `00`–`09` LLD); SQL is DDL text,
   not executed here.
2. **Cross-ref** — against `16` (the `notifications` entity, `emit_notification`, `16 §1.1` recipient floor,
   `16 §2.5` dedupe, `16 §3` scheduler, `16 §4` routes/in-app surface, `16 §4.2` in-app-only channel scope),
   `01 §3/§4.8` (`notification_kind`, `notifications` DDL — new tables flagged for `01 §4.9`), `09 §1.2`
   (`job_tenant`/RLS), `00-conv §3` (idempotency principle) + `§4` (`AsyncSession`), `02 §3/§4` (transient vs
   permanent + `Retry-After`), `11 §1` (worker pools/arq), `12 §1` (channel = 4th caller), `10 §9` (webhook
   transport precedent, ops metrics for dead letters).
3. **Consistency** — the external-delivery layer is **additive**: it never removes or weakens the parent's in-app
   record (the floor in §2 and the best-effort contract in §4 preserve `16`'s guarantees). The `16 §4.2`
   "in-product channel" statement is extended, not contradicted — external channels are an opt-in fan-out *of the
   same row*, flagged explicitly in the intro.
4. **New schema (`notification_deliveries`, `notification_prefs`, `delivery_channel`/`delivery_status` enums)** is
   flagged for its canonical home in `01 §4.9` (mirrors how `16` flags the `notifications` DDL to `01 §4.8`); if
   the two ever disagree, **`01` wins** (`16 §1` rule).

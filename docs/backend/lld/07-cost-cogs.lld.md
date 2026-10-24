# 07 (LLD) — Cost & COGS

> **Parent:** [`../07-cost-cogs.md`](../07-cost-cogs.md) (the cost-control stack, `BudgetGuard`, the caching
> table, routing/shaping/batching, the worked example, the platform's own COGS).
> The parent has the architecture and the `BudgetGuard.reserve`/`reconcile` bodies. It leaves the supporting
> types (`Reservation`, `cap()`), the cache *predicates* it gates on (`is_cacheable_exact`, `is_qa_safe`), the
> 80/90% notification dedup, and the P2 concurrency reservation as prose/`...`. This doc fills those.

## What this adds over parent `07`

| Gap in `07` | Filled here |
|---|---|
| `Reservation`, `BudgetExceeded`, `self.cap(workspace_id)`, `month` referenced in `§3`, undefined | §1 — the types + `cap()` (reads `spend_limits`, 01) |
| 80/90% `budget_threshold` notification "deduped per threshold per month" (prose) | §2 — the dedup mechanism |
| `is_qa_safe()` (semantic-cache fence) + the exact-cache "effectively deterministic" gate are prose predicates | §3 — both as code reading the snapshot |
| P2 "eliminate the overshoot window (semaphore reservation)" named, not designed | §4 — the concurrency reservation |

Python 3.12, Pydantic **v2** + async Redis. Snippets compile (§5). `BudgetGuard.reserve`/`reconcile`,
`compute_cost`, the caching table/keys, routing/shaping: **see `07` — not restated.** `BudgetExceeded` is the
402 `AppError` in [`00-conventions.lld.md §1`](00-conventions.lld.md). `quantize_usd`:
[`02-provider-gateway.lld.md §7`](02-provider-gateway.lld.md). `notifications` DDL: `01 §4.9` / `16`.

---

## 1. `Reservation`, `cap()`, and the month key (fills `07 §3`)

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Reservation:
    workspace_id: str
    est_usd: float                        # the pre-call estimate held against the counter (07 §3)
    scope: str = "workspace"              # 'workspace' (P1) | 'endpoint' (P2) — matches spend_limits.scope (01)
    scope_id: str | None = None


def month_key() -> str:
    """The monthly spend bucket — UTC, so a tenant's cap resets on a stable boundary regardless of locale."""
    return datetime.now(timezone.utc).strftime("%Y-%m")


# BudgetExceeded lives in 00-conventions.lld.md §1.2 (AppError, 402 budget.exceeded) — imported, not redefined.
```

`cap()` resolves the ceiling from `spend_limits` (`01 §4.8`), with the settings default as the floor:

```python
class BudgetGuard:   # reserve()/reconcile() bodies: see 07 §3 (now awaited — 03 LLD §2 / C9 fix)
    def __init__(self, db, redis, default_cap_usd: float):
        self.db, self.redis, self.default_cap = db, redis, default_cap_usd

    def cap(self, workspace_id: str) -> float:
        """Monthly USD ceiling for the workspace-scope row (01 spend_limits). P1 enforces the 'workspace'
        scope only; 'endpoint' rows are P2 (07 §3). Missing row → the settings default (00 §6)."""
        row = self.db.execute(
            select(SpendLimit.monthly_budget_usd)
            .where(SpendLimit.workspace_id == workspace_id, SpendLimit.scope == "workspace")
        ).scalar_one_or_none()
        return float(row) if row is not None else self.default_cap
```

`reserve()`'s `est_usd` (`07 §3`: "from `max_tokens` × profile pricing") comes from the same `compute_cost` the
gateway uses, on an estimated `NormalizedUsage` (output = `max_tokens`, input = prompt tokens). It stays a
**`float`** — the Redis spend counter is `incrbyfloat` (`07 §3`), so `Reservation.est_usd: float`. `quantize_usd`
(`02` LLD §7) is for the **persisted** `numeric(12,6)` cost on the run/span, **not** the in-flight Redis
reservation — don't quantize into the counter (mixing `Decimal` with `incrbyfloat` is a type error).

---

## 2. Budget-threshold notification dedup (fills `07 §3`)

`07 §3`: on `reconcile`, when spend crosses **80% / 90%** of the monthly cap, emit a `budget_threshold` owner
notification, "deduped per threshold per month." The mechanism — the dedup rides the `notifications` unique
index (`01 §4.9`: unique `(workspace_id, recipient_subject, dedupe_key) where read_at is null`):

```python
THRESHOLDS = (0.80, 0.90)       # 100% is the 402 hard-cap (07 §3), not a notification

async def maybe_notify_budget(workspace_id: str, prev_spent: float, spent: float, cap: float, *, db) -> None:
    """Called inside reconcile with the counter value BEFORE and AFTER this run's delta. Fires ONLY when this
    reconcile CROSSES a threshold upward (prev < t ≤ now). A budget threshold is a STANDING condition polled
    every reconcile, so the unread-only partial index alone would re-fire after the owner reads the notice
    (review finding) — crossing-detection fires exactly once per crossing regardless of read state."""
    if not cap:
        return
    prev_frac, frac = prev_spent / cap, spent / cap
    for t in THRESHOLDS:
        if prev_frac < t <= frac:                                         # upward crossing only
            dedupe = f"budget:{workspace_id}:{month_key()}:{int(t*100)}"   # per workspace, per month, per threshold
            for recipient in budget_recipients(workspace_id, db):          # owners + ws publishers/admins (16 §1)
                emit_notification(                                         # ON CONFLICT DO NOTHING on the unique index
                    db, workspace_id=workspace_id, recipient_subject=recipient,
                    kind="budget_threshold", severity="warning" if t < 0.90 else "critical",
                    title=f"Workspace at {int(t*100)}% of monthly budget", dedupe_key=dedupe,
                )
```

- **Fires on upward crossing, deduped per month:** `maybe_notify_budget` emits only when this reconcile
  crosses a threshold (`prev < t ≤ now`), so a standing over-threshold condition doesn't re-fire every
  reconcile — crucially, it doesn't re-fire after the owner *reads* the notice (the failure the unread-only
  index alone had: once `read_at` is set the row leaves the partial index and the next reconcile would
  re-insert). The `dedupe_key` `(workspace, month, threshold)` is belt-and-suspenders against a same-instant
  double crossing; a new `month_key()` resets it.
- **Per-recipient** (as `01 §4.9` requires): `recipient_subject` is in the key, so each recipient gets their
  own (deduped) copy — fixing the "only one person ever notified" trap.
- These are **owner** notices (`16`), distinct from the ops `SpendAnomaly` page (`10 §5`); both read the same
  reservation math. 100% is the 402 hard-cap, not a notification.

---

## 3. Cache predicates (fills `07 §4.1` / `§4.3`)

The parent gates the exact and semantic caches on prose predicates. Both read the frozen snapshot:

```python
def is_cacheable_exact(snapshot) -> bool:
    """Exact response cache (07 §4.1) is safe ONLY for an effectively-deterministic version:
    temperature 0, no tools (non-deterministic side effects), no retrieval (KB content is LIVE, 04 §9 — a
    cached answer could reflect stale chunks). Any of these → do not exact-cache."""
    p = snapshot.model_config.params
    return (
        (p.temperature == 0)
        and not snapshot.tool_bindings            # tool calls aren't replayable from a cached string
        and not snapshot.retrieval_bindings       # RAG answer can go stale on a KB refresh (04 §9) — time-sensitive
    )


def is_qa_safe(snapshot) -> bool:
    """Semantic cache (07 §4.3) returns a PREVIOUS answer for a SIMILAR query — a near-miss is a wrong
    answer. BANNED for tool-calling, citation/RAG, and security agents. Only stable FAQ-style Q&A qualifies.
    (semantic_cache_enabled is the owner's explicit per-agent opt-in on top of this — 07 §4.3.)"""
    return (
        not snapshot.tool_bindings                       # a tool agent must actually call the tool
        and not snapshot.io_contract.include_citations   # citation/RAG agent — a stale cite is a lie
        and not snapshot.retrieval_bindings              # RAG at all → not QA-safe
        and not _is_security_classified(snapshot)        # SOC/triage etc. — near-miss = wrong security decision
    )
```

- `is_cacheable_exact` keys the exact cache (`07 §4.1 exact_key`); a version that fails it is simply never
  exact-cached (TTL moot).
- `is_qa_safe` is the structural fence *under* the owner opt-in — so even an owner who flips the opt-in on a
  tool/RAG/security agent gets no semantic hits. `_is_security_classified` reads the agent's classification
  (a tag / access-policy / workspace marker); until a formal flag exists it defaults **conservative** (treat
  unknown as security-classified when the workspace is security-tiered).

> ⚠️ **Amendments (2026-10-24 review), folded into `01`:** (1) `semantic_cache_enabled` is now a
> `VersionSnapshot` field (`01` LLD §1) — the parent `07 §4.3`'s `snapshot.semantic_cache_enabled` had no home
> in the data model until now. (2) `is_qa_safe`/`is_cacheable_exact` are **free functions** here, so the parent
> `07 §4.3` method-call form `snapshot.is_qa_safe()` should read `is_qa_safe(snapshot)` — `VersionSnapshot` is
> a frozen dataclass with no such method.

> Note: no parent amendment needed — the parent already fences semantic cache correctly (`00 §7.4`); this just
> makes the two predicates it calls concrete, erring toward refusing the cache when classification is unknown.

---

## 4. P2 concurrency reservation (fills `07 §3`)

`07 §3` ships a single monthly counter in P1 with **bounded overshoot** (concurrent reservations can exceed
the cap by `in-flight × est`); the hard-cap backstop refuses once *real* spend crosses the ceiling. P2
eliminates the overshoot window. The design:

```python
# P2: a per-workspace concurrency SEMAPHORE + a reservation that counts in-flight estimate, so N concurrent
# runs can't collectively blow the cap before any of them reconciles.
async def reserve_p2(self, workspace_id, est_usd) -> Reservation:
    slots = self.concurrency_cap(workspace_id)                 # concurrency_limits (01 §4.8), P2
    if not await self._acquire_slot(workspace_id, slots):      # token-bucket of `slots` concurrent runs (Redis)
        raise RateLimited("workspace concurrency cap reached")  # 429 (00-conv §1)
    # count the estimate as IN-FLIGHT (separate key) so the cap check sees committed + in-flight spend:
    inflight = await self.redis.incrbyfloat(f"inflight:{workspace_id}:{month_key()}", est_usd)
    committed = await self._committed(workspace_id)
    if committed + inflight > self.cap(workspace_id):
        await self.redis.incrbyfloat(f"inflight:{workspace_id}:{month_key()}", -est_usd)
        await self._release_slot(workspace_id)
        raise BudgetExceeded()                                  # 402 — now BEFORE the overshoot, not after
    return Reservation(workspace_id, est_usd)
# reconcile_p2 moves est from the inflight key to the committed counter and releases the slot.
```

- **P1 → P2 is additive:** P1 is the single `spend:{ws}:{month}` counter (`07 §3`); P2 adds an
  `inflight:{ws}:{month}` counter + a concurrency semaphore (`concurrency_limits`, `01 §4.8`, created-in-P1-used-in-P2
  per `01 §1`). The cap check becomes `committed + inflight ≤ cap` — so the window where N concurrent runs each
  pass the check independently is closed.
- **Concurrency cap → 429** (`rate.limited`, `00-conv §1`), distinct from the **402** budget cap — matching
  the `08 §1` split (`07 §3`: "rate-limit is a separate 429").
- Ships behind the P2 flag; P1 keeps the cheap one-counter check + hard-cap backstop.

---

## 5. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as the other LLDs).
2. **Cross-ref** — against `07` read in full: `BudgetGuard`/`reserve`/`reconcile`/`Reservation`/`cap`/`month`
   (`§3`), caching table + `exact_key`/`is_qa_safe` gate (`§4`), eval COGS (`§5`), routing/shaping (`§6/§7`);
   `01 §4.8` (`spend_limits`/`concurrency_limits`) + `§4.9` (`notifications` unique index); `00-conventions.lld.md
   §1` (`BudgetExceeded` 402 / `RateLimited` 429); `02` LLD §7 (`quantize_usd`); `04 §9` (KB content live →
   not exact-cacheable); `16` (owner notifications).
3. **No parent amendment** — `07` was internally consistent; this doc only makes its named-but-undefined
   helpers/predicates concrete. (The earlier C9 `BudgetGuard` `await` fix lives in `03` LLD §2, not here.)

→ Next LLD: `04-rag.lld.md` (subtask 9, the last).

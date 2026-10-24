# 00 (LLD) — Cross-cutting Conventions: Errors, Pagination, Idempotency

> **Parent:** [`../00-overview.md`](../00-overview.md) + [`../08-api-surface.md §1`](../08-api-surface.md).
> `08 §1` fixes the error *envelope* and a 12-row exception→HTTP table, a one-line pagination rule, and the
> idempotency rule — but the **exception classes themselves** live scattered across `02`/`05`/`06`/`07`, the
> cursor codec is unspecified, and the idempotency concurrency case is open. This doc is the single place
> every router and worker imports those primitives from, so they don't get re-invented per module.

## What this adds over parents `00`/`08`

| Gap | Filled here |
|---|---|
| Exception *names* scattered (`ConflictError` 05, `BudgetExceeded` 07, `ProviderUnavailable`/`RetryableProviderError`/`NonRetryableError` 02, `OutputBlocked` 06); `08 §1` asserts `api/errors.py` exists but never shows it | §1 — one `AppError` hierarchy, the `errors.py` handler, and a row-by-row map of the `08` table to a class. Plus the **three-category taxonomy** (HTTP-surfacing vs internally-caught vs run `stop_reason`) that no parent states. |
| `08 §1` pagination is one line (`?limit&cursor` → `{items,next_cursor}`); no cursor encoding, no filter/sort convention | §2 — `Page[T]`, the opaque-cursor codec, limit bounds, and the per-router filter/sort whitelist pattern. |
| `08 §1` idempotency: the happy path + TTL replay; the **two-concurrent-same-key** race is undefined | §3 — the full check/reserve/replay flow, the Redis key + TTL, and the in-flight case (a flagged new code). |

Python 3.12, `from __future__ import annotations`, stdlib + Pydantic v2 + FastAPI. Snippets compile (§4).

---

## 1. Exception taxonomy

The parents conflate three *different* failure kinds under "errors." Keeping them distinct is the whole
point — only the first category becomes an HTTP error body:

| Category | Reaches the client as | Examples | Where handled |
|---|---|---|---|
| **A. HTTP-surfacing** (`AppError`) | the `08` error envelope, a 4xx/5xx | `AccessDenied`, `EvalGateError`, `BudgetExceeded` | `api/errors.py` handler |
| **B. Internally caught** | never an HTTP body — converted upstream | `RetryableProviderError`→retry (02), `OutputBlocked`→a `blocked` run (06) | gateway (02 §6) / graph (03) |
| **C. Run `stop_reason`** (not an exception) | a field on the Run + a `done` SSE frame, HTTP **200** | `deadline_exceeded`, `tool_limit`, `subagent_depth_exceeded`, `approval_denied`, `blocked` | runtime (03 §4/§5) |

`08 §1` already notes (C): "a sub-agent depth/fan-out breach is a run `stop_reason` … **not** an HTTP error."
This generalizes it. A guardrail BLOCK is the subtle one: `OutputBlocked` (B) is raised inside the graph,
caught, and turned into `run_status='blocked'` + `stop_reason='blocked'` (C) — the invoke call still returns
200 with a blocked outcome in the stream, never a 4xx.

### 1.1 The `AppError` base + handler

```python
from __future__ import annotations
from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """Base for every HTTP-surfacing error. Subclasses set http_status + code; `detail` is the optional
    machine-readable context object from the 08 envelope."""
    http_status: int = 500
    code: str = "internal"

    def __init__(self, message: str, *, detail: dict | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def envelope(self) -> dict:
        body: dict = {"code": self.code, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return {"error": body}       # matches 08 §1 exactly


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content=exc.envelope())


# Registered in api/main.py:
#   app.add_exception_handler(AppError, app_error_handler)
#   app.add_exception_handler(RequestValidationError, validation_error_handler)  # pydantic/FastAPI → 400 input.invalid
```

### 1.2 The concrete classes (one per `08 §1` row)

```python
class InputInvalid(AppError):           # 08: "ValidationError (input contract)"
    http_status, code = 400, "input.invalid"

class AuthRequired(AppError):
    http_status, code = 401, "auth.required"

class AccessDenied(AppError):           # not a member / RBAC / access-policy deny; also the test-invoke dry-run deny
    http_status, code = 403, "access.denied"

class NotFound(AppError):               # missing row AFTER tenant scope (never leaks cross-tenant existence)
    http_status, code = 404, "not_found"

# --- 409 family: same status, distinct codes ---
class ConflictError(AppError):          # generic 409 base (05 raises ConflictError today — see §1.3)
    http_status, code = 409, "conflict"

class EvalGateError(ConflictError):
    code = "publish.eval_gate"

class LowConfidenceError(ConflictError):
    code = "publish.low_confidence"

class RegistrationGate(ConflictError):  # 14 §2
    code = "registration.gate"

class ToolApprovalDenied(ConflictError):  # 03 §2 approval gate denied/expired at publish/invoke surface
    code = "tool.approval.denied"

class AssertionDetailRequired(AppError):  # 05 §2a — regex/required_fields/tool_called/exact need `detail`
    http_status, code = 422, "eval.assertion_detail_required"

class BudgetExceeded(AppError):         # 07 §3 — hard monthly cap
    http_status, code = 402, "budget.exceeded"

class RateLimited(AppError):            # per-token P1; concurrency+spend quota P2
    http_status, code = 429, "rate.limited"

class ProviderUnavailable(AppError):    # 02 §6 — provider + all fallbacks failed
    http_status, code = 502, "provider.unavailable"
```

### 1.3 Reconciliation with the parent docs

- **`05 §5`** raises `ConflictError(409, "publish.eval_gate: …")` — code baked into the message string.
  Under this hierarchy that becomes `raise EvalGateError("no PASS eval newer than the last config change",
  detail={"last_trigger_at": ..., "last_pass_at": None})`, with `code` as a first-class field the envelope
  reads. `LowConfidenceError` likewise replaces the `publish.low_confidence` string form. **No behavior
  change** — same status, same code, cleaner construction. (Flagged for the `05` LLD to adopt.)
- **`02 §6`** `RetryableProviderError` / `NonRetryableError` are **category B** — caught by the gateway retry
  loop; they are *not* `AppError`s and never hit `errors.py`. Only `ProviderUnavailable` (raised when retries
  + fallback are exhausted) is category A. This doc makes `ProviderUnavailable` an `AppError`; the two
  internal ones stay plain exceptions defined in `runtime/providers/`.
- **`06`** `OutputBlocked(label)` is **category B→C**: caught in `output_guardrails`, converted to
  `run_status='blocked'`. Not an `AppError`.
- **`07 §3`** `BudgetExceeded` is category A (402) — moved here so routers and the runtime budget guard share
  one class.

> ⚠️ **Amendment to `08 §1`:** the error table has no `internal`/500 row, yet `AppError.code` defaults to
> `"internal"`. Add an `unhandled → 500 internal` row (any non-`AppError` exception → logged + generic 500
> envelope, no stack to the client) so the envelope contract is total. Flagged, not edited.

---

## 2. Pagination & filtering

`08 §1`: cursor-based `?limit=50&cursor=<opaque>` → `{items, next_cursor}`; metrics use dedicated endpoints
(§7), not lists. This fixes the codec and the filter/sort convention.

```python
from __future__ import annotations
import base64, json
from typing import Generic, TypeVar
from pydantic import BaseModel

T = TypeVar("T")
DEFAULT_LIMIT, MAX_LIMIT = 50, 200


class Page(BaseModel, Generic[T]):
    items: list[T]
    next_cursor: str | None = None          # null ⇒ last page


def encode_cursor(sort_value: str, row_id: str) -> str:
    """Opaque, stable cursor. Encodes the (sort_key, id) of the LAST row on the page so the next query is a
    keyset seek, not an OFFSET (which drifts under concurrent inserts)."""
    raw = json.dumps({"s": sort_value, "id": row_id}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii")


def decode_cursor(cursor: str | None) -> tuple[str, str] | None:
    if not cursor:
        return None
    try:
        d = json.loads(base64.urlsafe_b64decode(cursor.encode("ascii")))
        return d["s"], d["id"]
    except Exception as exc:                 # malformed cursor is a client error, not a 500
        raise InputInvalid("invalid cursor", detail={"cursor": cursor}) from exc


def clamp_limit(limit: int | None) -> int:
    return max(1, min(limit or DEFAULT_LIMIT, MAX_LIMIT))
```

**Keyset pattern** (why not OFFSET): list on a stable sort (`created_at desc, id desc` — the indexes already
exist, `01 §8`). A cursor carries the last row's `(created_at, id)`; the next page is
`WHERE (created_at, id) < (:s, :id) ORDER BY created_at desc, id desc LIMIT :n+1` (fetch `n+1`, the extra row
tells you whether to emit a `next_cursor`). Immune to inserts between pages.

**Filtering/sorting — whitelist per router, never free-form.** Each list endpoint declares the columns it
accepts as filters and the sort keys it supports; anything else is `400 input.invalid`. This keeps a client
from filtering on an unindexed column or injecting a sort expression. The *specific* whitelist per endpoint
(e.g. `GET /runs` filters `status`/`agent_id`/date range) is defined in the `08` LLD — here is the shared
guard:

```python
def validate_sort(sort: str | None, allowed: set[str], default: str) -> str:
    key = sort or default
    if key.lstrip("-") not in allowed:       # leading '-' = descending
        raise InputInvalid("unsupported sort key", detail={"sort": sort, "allowed": sorted(allowed)})
    return key
```

---

## 3. Idempotency

`08 §1` + `02 §6` + `01 §4.7`: published invoke accepts `Idempotency-Key`; dedupe rides the partial-unique
index `runs (workspace_id, idempotency_key) where idempotency_key is not null`; the first completion's body is
cached in a short-TTL Redis store keyed `(workspace_id, Idempotency-Key)` and replayed verbatim; after TTL a
replay returns run metadata + `result_expired`. Langfuse is never the authoritative response source.

### 3.1 The flow (fills the mechanism)

```python
from __future__ import annotations

IDEMPOTENCY_TTL_S = 24 * 3600            # design choice: 24h replay window; after that → result_expired


async def invoke_idempotent(ws_id: str, key: str | None, run_fn, *, redis, db):
    """Wraps a published invoke. run_fn() performs the real runtime.invoke + persists the run.
    Three outcomes: fresh run, verbatim replay, or (post-TTL) a metadata-only expired marker."""
    if key is None:
        return await run_fn()                               # no key ⇒ no dedupe

    rkey = f"idem:{ws_id}:{key}"
    # 1. Reserve atomically. SET NX wins the race between two concurrent same-key requests.
    reserved = await redis.set(rkey, _IN_PROGRESS, nx=True, ex=IDEMPOTENCY_TTL_S)
    if not reserved:
        cached = await redis.get(rkey)
        if cached == _IN_PROGRESS:
            raise IdempotencyInProgress("a request with this Idempotency-Key is still running")  # ⚠️ see below
        if cached is not None:
            return _replay(cached)                           # 2. verbatim replay of the first completion
        # 3. Redis entry gone (TTL) but the runs row remains → metadata-only + result_expired
        run = await _find_run(db, ws_id, key)
        return _expired_marker(run)

    try:
        result = await run_fn()                              # the real invoke; inserts the runs row (unique index backstops Redis)
        await redis.set(rkey, _serialize(result), ex=IDEMPOTENCY_TTL_S)
        return result
    except Exception:
        await redis.delete(rkey)                             # free the key so a retry can succeed
        raise
```

### 3.2 Guarantees & the two-request race

- **Single-key, sequential replay:** second call after the first *completed* → `_replay` of the stored body.
- **Single-key, concurrent:** `SET NX` makes exactly one request the writer; the loser sees `_IN_PROGRESS`.
  `08 §1` doesn't define this case. **Decision:** return a new `409 idempotency.in_progress` rather than
  block-and-wait (simpler, no held connection; the client retries). The `runs` partial-unique index is the
  durable backstop if Redis is unavailable — a duplicate insert raises, caught and mapped to the same code.
- **Post-TTL:** Redis key expired, `runs` row persists → `result_expired` marker with run metadata
  (cost/tokens/status), never a reconstructed output.
- **Failure:** on exception the reservation is deleted so a genuine retry isn't permanently blocked.

> ⚠️ **Amendment to `08 §1`:** add one row — `idempotency key still in-flight → 409 idempotency.in_progress`.
> It's the only idempotency outcome the table doesn't cover. New class:
> `class IdempotencyInProgress(ConflictError): code = "idempotency.in_progress"`.

---

## 4. Verification performed

1. **Snippet compile** — every `python` block run through `compile()` (§4 harness, same as `01` LLD).
2. **Consistency with `08 §1`** — every row of the parent exception table maps to exactly one class in §1.2
   (codes verbatim: `input.invalid`, `auth.required`, `access.denied`, `not_found`, `publish.eval_gate`,
   `publish.low_confidence`, `eval.assertion_detail_required`, `registration.gate`, `tool.approval.denied`,
   `budget.exceeded`, `rate.limited`, `provider.unavailable`); the envelope shape matches `08 §1` byte-for-byte.
3. **Cross-ref** — exception origins checked against the parents actually read: `ConflictError` (05 §5),
   `BudgetExceeded` (07 §3), `ProviderUnavailable`/`RetryableProviderError`/`NonRetryableError` (02 §6),
   `OutputBlocked` (06), `registration.gate` (14 §2 via 08), idempotency index (01 §4.7).
4. **Two flagged amendments** (500/`internal` row; `idempotency.in_progress` row) recorded as callouts, not
   parent edits.

→ Next LLD: `02-provider-gateway.lld.md` (subtask 3).

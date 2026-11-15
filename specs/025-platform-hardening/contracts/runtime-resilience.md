# Contract — Runtime provider resilience (`runtime/providers/resilience.py` + gateway)

Pure library contract (P6: no FastAPI/redis/prometheus imports — `test_runtime_purity.py` keeps guarding it).
Types are defined in [data-model.md §A](../data-model.md). Every numbered behaviour below maps to ≥1 Red test.

## 1. Gateway signatures (additive)
```python
async def gateway.stream(req, *, api_key, profile=None, timeout=None,
                         guard: Guard | None = None, traffic_class: TrafficClass = "live",
                         ) -> AsyncIterator[NormalizedStreamEvent]
async def gateway.embed(texts, *, provider, model, api_key, timeout=None, region=None,
                        guard: Guard | None = None, traffic_class: TrafficClass = "live",
                        ) -> EmbeddingResult            # raises EmbeddingError
```
`guard=None` ⇒ `default_guard()` (module-level lazily built `Guard(ResiliencePolicy(), LocalBreakerStore())`).
`runtime.invoke` passes `guard=ctx.guard` and `traffic_class=traffic_class(ctx.source)`; the retrieval step passes the
same for query embeddings; `ingest_executor` passes `traffic_class="batch"`.

## 2. Stream attempt loop — observable behaviour
| # | Given | Then |
|---|-------|------|
| S1 | transient `TransportError` before any surfaced event, attempts left, deadline allows | wait (D3) and retry; nothing yielded for the failed attempt |
| S2 | success on attempt k | events stream once; terminal `USAGE.data["attempts"] == k` |
| S3 | non-transient `TransportError` (`retryable=False`) | single ERROR, `attempts == k`, no retry, breaker untouched |
| S4 | transient error **after** a surfaced `TEXT_DELTA` / `REASONING_DELTA` / `TOOL_CALL_*` | single ERROR, no retry, no duplicated text (F10/FR-005); counts as a breaker failure |
| S5 | `max_attempts` exhausted | ERROR of the last failure, `attempts == max_attempts` (never more) |
| S6 | next wait would cross `deadline − min_attempt_budget_s` | stop, ERROR of the last failure (no doomed attempt) |
| S7 | `Retry-After` valid | wait = `min(retry_after, per_wait_cap_s)`; invalid/≤0/past ⇒ backoff |
| S8 | stream ends with no USAGE (degraded) | existing degraded ERROR, never retried, `retryable=False` |
| S9 | breaker refuses | ERROR `{type:"provider_unavailable", retryable:true, attempts:0, message:"provider temporarily unavailable (circuit open)"}` — **zero** transport calls |
| S10 | FileNotFoundError / ModelRetiredError | raised before admission exactly as today (breaker untouched) |

## 3. Breaker behaviour
| # | Given | Then |
|---|-------|------|
| B1 | N consecutive transient failures on `(p, m, cls)` | next `admit` ⇒ refused until cooldown |
| B2 | cooldown elapsed, K concurrent callers | exactly 1 `trial`, K−1 refused |
| B3 | trial succeeds | closed for every instance sharing the store |
| B4 | trial fails | re-opened for another cooldown |
| B5 | `(p, mA)` open | `(p, mB)` unaffected |
| B6 | live open | batch on same `(p, m)` refused (FR-011) |
| B7 | batch open | live on same `(p, m)` admitted |
| B8 | a success between failures | consecutive count resets |
| B9 | store raises | local store used for this call, observer gets `breaker_degraded`; never refused *because of* the outage |

## 4. Batch bulkhead
| # | Given | Then |
|---|-------|------|
| K1 | > cap concurrent batch calls on `(p, m)` | at most `cap` inside the attempt loop; the rest wait (observer `batch_wait` with seconds) |
| K2 | live calls | never take the semaphore |
| K3 | waiting exceeds remaining deadline | the outer deadline (invoke `asyncio.timeout`) ends it as `TIMEOUT` — never a silent hang |

## 5. Embeddings
`gateway.embed` applies S1–S7/S9/B* per **batch request**; vectors are only appended after a batch succeeds, so a
retry never duplicates vectors. Refusal ⇒ `EmbeddingError(type="provider_unavailable", retryable=True)`.
`EmbeddingResult` and `EmbeddingError` carry `attempts` (summed over batches).

## 6. Transport seam (`runtime/providers/transport.py`)
- Forces `max_retries=0` and `num_retries=0` on `litellm.acompletion` / `litellm.aembedding` (G1) — via
  `kwargs.setdefault` so a test can observe them.
- `TransportError.retry_after` parsed from `exc.headers` then `exc.response.headers` (`retry-after`, case-insensitive;
  seconds or HTTP-date; `≤0`, past, non-finite, unparseable ⇒ `None`).
- `_is_retryable`: 409 removed from the transient set (D4).

## 7. Observer events (runtime → API metrics; names are the contract, labels bounded)
| name | labels | when |
|------|--------|------|
| `retry` | provider, model, cls | before each retry wait |
| `breaker_transition` | provider, model, cls, to ∈ {open, half_open, closed} | on state change observed by this instance |
| `breaker_refused` | provider, model, cls | on S9 |
| `breaker_degraded` | — | on B9 |
| `batch_wait` | provider, model, seconds (value) | after K1 acquisition |

Observer exceptions are swallowed (telemetry never alters a call — 023 FR-009 precedent).

## 8. Run record mapping (`runtime/invoke.py`)
- ERROR `type == "provider_unavailable"` ⇒ `Outcome.PROVIDER_UNAVAILABLE`; cost `0`/`None` (no usage), zero tokens.
- Transient failure after the first surfaced event ⇒ `Outcome.PROVIDER_ERROR` (existing mapping, pinned by spec FR-005; not `DEGRADED`, which stays reserved for usage-less streams).
- Cost basis (FR-015): `cost_usd` = Σ priced *reported* usage; a failed attempt with no reported usage contributes 0 and is disclosed through `retries`, never claimed as the exact provider charge.
- `RunRecord.retries` = Σ(attempts − 1) over turns and embedding calls (refusal attempts=0 contributes 0).
- Correction turns (validated mode) each run their own attempt loop; the deadline is shared (one `asyncio.timeout`).

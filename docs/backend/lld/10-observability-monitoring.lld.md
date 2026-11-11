# 10 (LLD) — Observability & Monitoring

> **Parent:** [`../10-observability-monitoring.md`](../10-observability-monitoring.md) (the one-rule per-call
> log contract, the four-store split, the `span()` helper, `SpanRecord`, metrics/SLOs/alerts, the cost↔COGS
> bridge, drift/quality feedback, async-job status, provider-resilience monitoring).
> The parent fixes the *topology and the intent*, but leaves the load-bearing wiring to the implementer: it
> shows `SpanRecord` and a `_emit_langfuse` that mints a `generation` for **every** span kind yet **never
> creates the Langfuse trace**, never says who tags it with `run_source`; it names the spec-A2 per-call log
> line as a hard contract (`{model, prompt_version, input, output, latency_ms, cost_usd, success}`) but
> **never actually emits it anywhere**; it asserts "PII-redacted before write" with **no redaction path**; it
> calls `_persist_span` **per span** while `03 §2`/`00-conv §4.2` persist spans **with the run at close**
> (an unreconciled contradiction); and it never states that telemetry emission must be **internally caught**
> so a Langfuse/Prometheus blip can't break `invoke()`. This doc makes all of that buildable.

## What this adds over parent `10`

| Gap in `10` | Filled here |
|---|---|
| `span()` writes a `spans` row **per span** (`§1`), but `03 §2 _close_run` + `00-conv §4.2` persist **Run + Spans together** at close — which wins? | §1 — the four-sink split: **PG buffered + flushed with the run** (atomic, `03 §2`); Langfuse/Prometheus/JSONL **per-span, best-effort**. Amendment to `10 §1`. |
| `_emit_langfuse` (`§3`) mints a `generation` for every kind and **never creates the trace** / sets `run_source` | §2 — `_open_trace`/`_close_trace`, the `kind → observation-type` map, the per-kind required-attrs table, `run_source` tagging, sub-agent trace linkage. Amendment to `10 §3`. |
| spec-A2 log line is a **hard contract** (`§1`) but is **never emitted** — no shape, no sink, no retention | §3 — `ModelCallLog` (the exact JSONL schema), the field-by-field map to spec A2, where it's written + retained, and that it is a **projection, not an authority** |
| four stores (three SoR roles — money / what-was-said / time-series — + derived ClickHouse) but the **runs-row ↔ log ↔ trace** relationship + the single cost number are prose | §4 — the source-of-truth matrix; one quantized cost (`02 §7` `quantize_usd`) copied into every sink; reconciliation by `run_id`/`trace_id` |
| "PII-redacted before write" (`§7`/`09`) with **no mechanism** | §5 — `redact_for_telemetry` **reusing the `06` tier-0 PII+secret scrub**; the never-logged list (Vault keys, tool-arg secrets) |
| `_emit_metrics` (`§4`) is a `# Prometheus` comment; `ac_cost_usd_total` is a float Counter vs `numeric(12,6)`; label cardinality unbounded | §6 — the body, the float-approx-vs-PG-authoritative cost rule, the cardinality bound |
| no error taxonomy for the **telemetry path itself** — a Langfuse outage must not 500 a run | §7 — `_safe_emit` (category B, `00-conv §1`), the PG-persist-failure handling, `ac_telemetry_emit_failures_total` |
| retention tabled (`§2`); **sampling** absent, and it collides with "every call logged" | §8 — PG/JSONL **never sampled** (A2); **Langfuse v3 100% (no sampling — columnar)**; retention-per-store; the P2 `analytics` ClickHouse DB (ReplacingMergeTree + AggregatingMergeTree MVs + TTL, derived/no-RLS) |
| tenancy of telemetry only implied (`workspace` labels); who may read Langfuse/Prometheus; org-plane read | §9 — RLS on `runs`/`spans`; Langfuse/Prometheus/Grafana **operator-only**; the `trace_id` deep-link gate; org plane via the `15 §2` `SECURITY DEFINER` read |

Python 3.12, `from __future__ import annotations`, Pydantic v2 for the boundary schema. Snippets compile (§11).
`SpanRecord`/`span()`/`set_usage`/`_emit_langfuse`/`_emit_metrics`: **see `10` — not restated.**
`NormalizedUsage` (fields `input_tokens`/`output_tokens`/`cached_input_tokens`/`cache_write_tokens`/
`reasoning_tokens`/`cost_usd`): [`02-provider-gateway.lld.md §7`](02-provider-gateway.lld.md) + `02 §2`.
`RunContext` (`workspace_id`/`run_source`/`trace_id`/`session_id`/`principal`): [`01-data-model.lld.md §2`](01-data-model.lld.md).
`quantize_usd` / `attach_cost`: [`02-provider-gateway.lld.md §7`](02-provider-gateway.lld.md). `invoke()` persist/
reconcile order + `AsyncSession`: [`00-conventions.lld.md §4`](00-conventions.lld.md). Redactors: [`06-guardrails.lld.md §2`](06-guardrails.lld.md).

---

## 1. One `SpanRecord`, four sinks — who is authoritative, who is best-effort

The parent's `span()` (`10 §1`) does three things on exit: persist a `spans` row, mirror Langfuse, bump
Prometheus. This LLD adds the **fourth** sink (the spec-A2 JSONL line, §3) and pins the **split that the parent
left ambiguous**: which writes are *durable and atomic with the run*, and which are *fire-and-forget*.

| Sink | Holds | Durability | Written | Authoritative for |
|---|---|---|---|---|
| **Postgres** `runs`+`spans` (`01 §4.7`) | tenant-attributed call records, tokens, `cost_usd numeric(12,6)` | transactional, **buffered + flushed with the run at `_close_run`** (`03 §2`) | once per run, in the run's txn | **money + tenancy** (billing, quota, org report) |
| **Langfuse** trace (`§2`) | full (redacted) I/O, nested observations, scores | best-effort, per-span | per `span()` exit | **"what was said"** (debug, judge calibration) |
| **Prometheus** (`§6`) | numeric counters/histograms | best-effort, per-span | per `span()` exit | **real-time ops** (SLO, alerting) |
| **JSONL log line** (`§3`) | redacted per-model-call record (spec A2) | best-effort, per **model** span | per model-span exit → stdout | **nothing** — a projection for ops/log search |

> ⚠️ **Amendment to `10 §1` (reconciles `03 §2` / `00-conv §4.2`).** The parent's `span()` calls
> `await _persist_span(ctx, rec)` **per span**, but `03 §2 _close_run` persists "final status + cost/tokens +
> **Spans**" in the run's `finally`, on one `AsyncSession` transaction (`00-conv §4.2` atomicity rule). A
> per-span inline write would mean N mid-stream DB round-trips (hurting the p95-first-token SLO) **and** a
> spans-vs-run write that isn't atomic. **Decision: `span()` accumulates `SpanRecord`s on `ctx` (in memory)
> and `_close_run` flushes them with the run in one transaction.** Only the **best-effort** mirrors
> (Langfuse/Prometheus/JSONL) fire per-span, so ops dashboards still update live. A crashed run loses its
> buffered spans *and* its (`pending`) run row together — consistent, never a half-written run.
>
> This needs two fields on `RunContext` (`01 §2`) the parent doesn't carry: **`span_buffer: list[SpanRecord]`**
> (a mutable list — fine on the frozen dataclass, `default_factory=list`) that `span()` appends to, and
> **`run_id: str`** (the `runs.id` from `_open_run`, threaded onto `ctx` so the JSONL line and spans can FK the
> run; distinct from the text `trace_id`). **(Now folded: `01` LLD §2 `RunContext` carries `span_buffer`/`run_id`;
> `03` LLD §2 flushes the buffer with the run at `_close_run` — 2026-10-25 reconciliation.)**

```python
# services/runtime/obs.py — the reconciled span() finally (fills 10 §1)
async def _on_span_exit(ctx: "RunContext", rec: "SpanRecord") -> None:
    ctx.span_buffer.append(rec)                 # PG: buffered → flushed by _close_run in the run txn (03 §2)
    _safe_emit(_emit_langfuse, ctx, rec)        # best-effort (§7): never raises into invoke()
    _safe_emit(_emit_metrics, ctx, rec)         # best-effort (§7)
    if rec.kind == "model":
        _safe_emit(_emit_call_log, ctx, rec)    # the spec-A2 JSONL line (§3), model spans only
```

`_close_run` (`03 §2`) then does the one durable write — `insert runs(...)`, `insert spans(...)` for the whole
buffer — on the request `AsyncSession`, before the Redis budget reconcile (`00-conv §4.2` order). `runs` is the
cost system-of-record; everything else copies from the same `SpanRecord`.

---

## 2. The Langfuse trace/span schema (fills `10 §3`)

The parent's `_emit_langfuse` mints a `generation` for *every* kind and **never opens the trace** — so there is
no place `run_source`, `workspace_id`, or the terminal status/cost live at the run level, and a guardrail/tool
step is mislabeled as an LLM generation. Fixed here.

**One trace per run.** `ctx.trace_id` (minted in `invoke()`, `00 §4`, stored on `runs.trace_id`, `01 §4.7`) is
the Langfuse trace id. `invoke()` opens it before the graph and closes it in `finally`:

```python
# services/runtime/obs.py (cont.)
def _open_trace(ctx: "RunContext", snapshot) -> None:
    _lf.trace(
        id=ctx.trace_id,
        name=f"{ctx.run_source}:{snapshot.agent_id}",
        session_id=ctx.session_id,                     # threads a multi-turn playground/channel session
        metadata={"workspace_id": ctx.workspace_id, "agent_id": snapshot.agent_id,
                  "version_id": snapshot.version_id, "run_source": ctx.run_source,
                  "parent_trace_id": ctx.parent_run_id},   # set for run_source="subagent" (01 runs.parent_run_id)
        tags=[ctx.run_source],                         # ← run_source tags the trace (filter playground vs api vs eval)
    )

def _close_trace(ctx: "RunContext", *, status: str, stop_reason: str | None, cost_usd) -> None:
    _lf.trace(id=ctx.trace_id, output=None,            # final roll-up; I/O already on the generations
              metadata={"status": status, "stop_reason": stop_reason,
                        "cost_usd": str(cost_usd)})     # a COPY of runs.cost_usd (§4) — never summed for billing
```

**`kind → Langfuse observation` map** — the parent's "always a generation" is wrong; only model/judge calls are
generations:

| `SpanRecord.kind` | Langfuse observation | Required `attrs` (beyond name/latency/success) |
|---|---|---|
| `model` | `generation` | `model`, `prompt_version` (`snapshot.prompt_hash`), `params`, `cache_hit`, `substitution[]` (`02 §6`), usage (`input/output/cached_input/cache_write/reasoning_tokens`), `cost_usd` |
| `judge` | `generation` | `judge_version` (`05`), `cost_usd` — **costed separately** (`10 §6`, judge is the COGS risk) |
| `retrieval` | `span` | `kb_refs[]`, `chunk_count`, `top_score`, `embedding_model` (`04`) |
| `tool` | `span` | `tool_name`, **redacted** `args` (`03 §4 redact()`), `success` |
| `guardrail` | `span` | `stage`, `decision` (ALLOW/BLOCK/REWRITE), `label`, `fail_mode`, `cost_tier` (`06`) |
| `skill` | `span` | `skill_name` (instruction-assembly, `03 §4`) |

```python
def _emit_langfuse(ctx: "RunContext", rec: "SpanRecord") -> None:
    common = dict(trace_id=ctx.trace_id, name=rec.name,
                  level=("ERROR" if not rec.success else "DEFAULT"),
                  metadata={"run_source": ctx.run_source, **rec.attrs})
    if rec.kind in ("model", "judge"):
        _lf.generation(**common, model=rec.attrs.get("model"),
                       input=redact_for_telemetry(rec.attrs.get("input"), ctx),      # §5 — redacted I/O
                       output=redact_for_telemetry(rec.attrs.get("output"), ctx),
                       usage={"input": rec.input_tokens, "output": rec.output_tokens,
                              "total_cost": rec.cost_usd})                            # our cost, not LiteLLM's (02)
    else:
        _lf.span(**common)                             # guardrail | tool | retrieval | skill
```

- **Sub-agent runs** (`run_source="subagent"`, `03 §6`) are their **own** run row + own trace (1:1 with `runs`),
  linked to the parent by `parent_trace_id` metadata (= `ctx.parent_run_id`, the parent `trace_id`). A reviewer
  reconstructs the tree from `runs.parent_run_id` (`01 §4.7`); traces are not nested inside one blob.
- **Scores** (`_lf.score`, `10 §6/§7`) attach eval verdicts (`05`) and in-product feedback to the trace by
  `trace_id` — unchanged from the parent.

> ⚠️ **Amendment to `10 §3`:** add `_open_trace`/`_close_trace` (the parent emits generations against a
> trace that is never created) and split `_emit_langfuse` by `kind` (generation for model/judge, span for the
> rest) — a guardrail step is not an LLM generation.

---

## 3. The per-call structured log line (fills the spec-A2 contract)

`10 §1` calls the spec-A2 line a "hard contract" but never emits it. It is the **model span, serialized as one
redacted JSON line to stdout**, picked up by the platform log pipeline (Loki/CloudWatch, `11`). It is a
**projection of the `SpanRecord`, not a source of truth** — `cost_usd` is the *same* quantized Decimal that
lands in `spans.cost_usd` (§4), serialized as a string to preserve the `numeric(12,6)` exactly.

```python
from __future__ import annotations
from decimal import Decimal
from pydantic import BaseModel
from runtime.providers.cost import quantize_usd      # 02 §7

_LOG_IO_CAP = 2048                                    # truncate I/O in the line; full (redacted) I/O lives in Langfuse

class ModelCallLog(BaseModel):
    # --- the spec-A2 contract, verbatim keys (scope.md §146 / spec.md §115) ---
    model: str
    prompt_version: str            # snapshot.prompt_hash
    input: str                     # REDACTED + truncated (§5)
    output: str                    # REDACTED + truncated (§5)
    latency_ms: int
    cost_usd: str                  # str(Decimal) — the quantized numeric(12,6), identical to spans.cost_usd (§4)
    success: bool
    # --- context so a line reconciles to its run/trace/tenant (never in the A2 list, always carried) ---
    run_id: str
    trace_id: str
    span_id: str
    workspace_id: str
    agent_id: str
    version_id: str
    run_source: str                # playground|api|eval|channel|tool_test|subagent (01 §3)

def _emit_call_log(ctx: "RunContext", rec: "SpanRecord") -> None:
    line = ModelCallLog(
        model=rec.attrs.get("model", rec.name),
        prompt_version=rec.attrs.get("prompt_version", ""),
        input=redact_for_telemetry(rec.attrs.get("input", ""), ctx)[:_LOG_IO_CAP],
        output=redact_for_telemetry(rec.attrs.get("output", ""), ctx)[:_LOG_IO_CAP],
        latency_ms=rec.latency_ms,
        cost_usd=str(quantize_usd(rec.cost_usd)),          # one quantization point (02 §7); matches the DB row
        success=rec.success,
        run_id=ctx.run_id, trace_id=ctx.trace_id, span_id=rec.span_id,
        workspace_id=ctx.workspace_id, agent_id=rec.attrs.get("agent_id", ""),
        version_id=rec.attrs.get("version_id", ""), run_source=ctx.run_source,
    )
    _logger.info(line.model_dump_json())                   # one JSON object per line → stdout → log pipeline (11)
```

- **Field map to spec A2** (`scope.md`: *model, prompt version, input, output, latency, cost, success/fail*):
  `latency`→`latency_ms`, `cost`→`cost_usd` (suffixed per `00 §8` money/time naming), `success/fail`→`success`
  (bool). The seven contract fields are present on **every** model call, from the **one** shared helper (`10 §1`
  "test == eval == prod") — so playground, published, channel, and eval lines are byte-identical in shape.
- **Where / retention:** stdout JSON → the log aggregator; retained ~**14–30d** (ops/debug horizon), shorter than
  the 90d `runs`/`spans` hot window (`11 §9`). The line is disposable; the billable record is the `runs` row.
- **One line per model call**, so an agentic multi-call run emits several — matching the per-`generation`
  Langfuse observations and the summed `runs.cost_usd`.

---

## 4. Source of truth & the single cost number

The four stores do **not** each compute cost independently; it is one figure (`02 §7`) copied into four
places. The matrix:

| Question | Authoritative store | Why |
|---|---|---|
| "What did this run cost / who pays?" | **`runs.cost_usd` / `spans.cost_usd`** (Postgres) | `numeric(12,6)`, transactional, tenant-scoped; drives quota (`07`) + org report (`15`) |
| "What exactly was said?" | **Langfuse trace** | holds the (redacted) I/O payloads kept out of the lean `runs` table (`10 §2`) |
| "What is the live error/latency rate?" | **Prometheus** | aggregate time-series; no per-run truth |
| "Grep a specific past call fast" | **JSONL line** | ops convenience; cites `run_id` to get back to the SoR |

**The cost rule (load-bearing, `02`/`07`/`10 §6`):** cost is computed **once** by the gateway from our
capability profiles (`02 §4.1 compute_cost`), **never** read from LiteLLM, quantized **once** via
`quantize_usd` at `attach_cost` time (`02 §7`), and attached to the `USAGE` event. From there the *same*
`Decimal` lands in `spans.cost_usd`, is summed into `runs.cost_usd`, copied as a string into the Langfuse
generation `total_cost` and trace metadata, and into the JSONL `cost_usd`. **No sink re-derives cost**, so the
dashboard, the quota engine, and the invoice can never disagree ("no estimated-vs-billed drift", `10 §6`).

```sql
-- /v1/metrics/spend (08) and the quota check (07) read the SoR; aggregate in SQL, never re-cost (10 §6)
select date_trunc('day', created_at) d, workspace_id, sum(cost_usd) usd, count(*) runs
from runs where created_at >= now() - interval '30 days' group by 1,2 order by 1;
```

Summing already-quantized `numeric(12,6)` values in Postgres is exact (`02 §7`: "never re-quantize downstream").

---

## 5. Redaction — reuse the `06` scrub, never log a raw secret

`10 §7`/`09` require redaction "before any log/trace write" but give no path. **There is exactly one redaction
primitive, shared with the `06` guardrails** — the tier-0 PII + secret-leak detectors (`06 §2`
`PiiStage`/`SecretLeakStage` call the same `_redact`). Telemetry reuses it; it does **not** invent a second
regex set that could drift from what the output guardrail enforces.

```python
# services/runtime/obs.py (cont.)
from runtime.guardrails.redact import redact_pii_and_secrets   # the SAME detectors 06 §2 uses (_redact)

def redact_for_telemetry(text: str | None, ctx: "RunContext") -> str:
    """Scrub PII + key-shaped/high-entropy secrets from any text before it reaches Langfuse I/O or a JSONL
    line (09 §9, 10 §7). Reuses the 06 tier-0 detectors so telemetry redaction == output-guardrail redaction —
    one source of truth for 'what is a secret'. Best-effort by contract: on failure, fall back to the empty
    string (log nothing) rather than risk leaking raw text (§7)."""
    if not text:
        return ""
    try:
        return redact_pii_and_secrets(text)
    except Exception:
        return "[redaction-failed]"            # fail CLOSED for telemetry: drop the payload, never leak it
```

**Never-logged list** (not merely redacted — *never present* in any sink):
- **Secrets-backend-resolved provider / per-user keys** (feature 014 — AWS Secrets Manager, not Vault; `09 §9`) — `resolve_provider_key` (`03 §4`, `09`) holds them in memory for
  the call only; they are never put on a `SpanRecord.attrs`, so they cannot reach §2/§3.
- **Tool-call arguments** are already scrubbed by `03 §4 redact(args)` *before* they land on a `tool` span, so
  the Langfuse `tool` observation carries redacted args by construction.
- **Idempotency keys / session ids** are identifiers, not secrets — logged (they're how a line reconciles).

Redaction runs on the **best-effort** path (§7): a redaction *failure* degrades to dropping the payload, it
never blocks or crashes the run.

---

## 6. Metrics emission + cardinality + the cost-label rule (fills `10 §4`)

The parent declares the metric families (`10 §4`) but `_emit_metrics` is a comment. The body, and the two
footguns the parent glosses:

```python
# services/runtime/obs.py (cont.) — the metric families are defined in 10 §4; this is the emit
def _emit_metrics(ctx: "RunContext", rec: "SpanRecord") -> None:
    if rec.kind == "model":
        TOKENS.labels(ctx.workspace_id, rec.attrs.get("model", "?"), "in").inc(rec.input_tokens)
        TOKENS.labels(ctx.workspace_id, rec.attrs.get("model", "?"), "out").inc(rec.output_tokens)
        COST.labels(ctx.workspace_id, rec.attrs.get("agent_id", "?"),
                    rec.attrs.get("model", "?")).inc(float(rec.cost_usd))   # float: ops-APPROX only (see rule)
    if rec.kind == "guardrail" and rec.attrs.get("decision") == "BLOCK":
        pass                                        # refusal/block-rate proxies (10 §7) derive from guardrail spans
    for sub in rec.attrs.get("substitution", []):   # provider-resilience (10 §8): a quietly-degrading provider
        PROV_ERR.labels(sub.get("provider", "?"), "substitution").inc()
# run-level metrics (REQ, FIRST_TOK, E2E, DEADLINE) are incremented by _close_run at finalize (10 §4), not per span.
```

- **Cost on Prometheus is a `float` approximation, by design.** `ac_cost_usd_total` is a float64 Counter — fine
  for a live spend-rate panel and the `SpendAnomaly` alert (`10 §5`), **but it is not a billing figure**. Every
  *authoritative* cost view (dashboards, `/v1/metrics/spend`, org report, quota) reads `sum(cost_usd)` from
  Postgres with `quantize_usd` precision (§4). This resolves the `float`-vs-`numeric(12,6)` mismatch the parent
  leaves open: Prometheus = approximate ops, Postgres = exact money.
- **Label-cardinality bound.** `workspace_id` as a label is safe only because tenants are bounded (internal
  platform, tens–hundreds). **`agent_id` is higher cardinality** — it is a label *only* on `COST` (the parent's
  choice) and must **not** be added to histograms; per-agent latency/volume slices come from Postgres
  (`/v1/metrics`, `10 §6`), not a Prometheus label explosion. Free-text (`caller`, `tool_name`, model *versions*)
  is **never** a label.
- **`ac_shadow_provider_calls_total{workspace,provider}`** (counter) — direct-to-provider calls **denied at the
  network egress boundary** (an attempted platform bypass). It is **not** span-driven: the egress-proxy sidecar
  emits it (`14` LLD §4.2 owns the definition), scraped into Prometheus with the rest of the egress/provider-
  resilience set (parent `10 §4/§8`); operator-scoped like every other Prometheus series (§9). It is **absent**
  when no egress proxy is deployed — a missing series reads as *unmeasured*, never `0` (`14` LLD §4.2). Feeds the
  adoption report's shadow column (`15` LLD / `14` LLD §4.3) and the `monitor`-mode `shadow_usage` nudge (§10).

---

## 7. Telemetry error taxonomy — a telemetry failure never breaks `invoke()`

The parent never states this, and it is the single most important safety property of the observability path: a
Langfuse outage, a Prometheus scrape stall, or a redaction bug **must not** turn a healthy run into an error.
Per the `00-conv §1` three-category model, **all best-effort telemetry emission is category B** (internally
caught, never an `AppError`, never a run `stop_reason`).

```python
from __future__ import annotations

def _safe_emit(fn, ctx: "RunContext", rec: "SpanRecord") -> None:
    """Wrap a best-effort sink (Langfuse / Prometheus / JSONL). Its failure is swallowed + counted, NEVER
    propagated — telemetry is observational, not on the serving critical path (00-conv §1 cat. B; 11 §9
    'observability data, not on the serving critical path')."""
    try:
        fn(ctx, rec)
    except Exception as exc:
        TELEMETRY_FAIL.labels(sink=getattr(fn, "__name__", "?")).inc()   # ac_telemetry_emit_failures_total
        _logger.warning("telemetry emit failed", extra={"sink": fn.__name__, "err": repr(exc)})
```

| Telemetry failure | Category | Handling |
|---|---|---|
| Langfuse down / timeout | B (best-effort) | `_safe_emit` swallows; `ac_telemetry_emit_failures_total{sink="_emit_langfuse"}`++; the run streams + persists normally |
| Prometheus client raises | B | swallowed + counted; metrics gap only |
| JSONL serialize / redaction fails | B | swallowed; `redact_for_telemetry` already fell back to `[redaction-failed]` (§5) — no raw leak |
| **PG `spans`/`runs` flush fails** at `_close_run` | **near-fatal, but post-stream** | NOT best-effort — this is the billing SoR. The stream has already reached the client; we log + **alert ops** (lost a billable/traced record) and re-raise inside `_close_run`'s own handler so the error is recorded, but we never "un-stream" the answer. Distinct from the three above. |

- **`ac_telemetry_emit_failures_total{sink}`** (new counter) feeds a low-priority ops alert: a *sustained* spike
  means the observability plane is degraded even though serving is healthy — exactly the condition that would
  otherwise go unnoticed.
- The PG-persist failure is the one telemetry write that is *not* fire-and-forget, because losing a `runs` row
  loses money + tenancy truth (§4); it is handled by `03 §2`'s `finally`, not here.

---

## 8. Retention (no sampling — Langfuse v3 columnar) + the Phase-2 `analytics` ClickHouse DB

**Trace sampling is gone.** The parent previously bounded Langfuse storage with a `TRACE_SAMPLE_RATE = 0.2`
head-sample (we dropped ~80% of trace payloads on the high-volume happy path). With **Langfuse v3 on a
ClickHouse columnar backend** (`10 §2`, `11 §1/§3`) that cost bound is no longer needed: traces are retained
at **100%**. This *over-satisfies* spec-A2 — the per-call model-call record (PG `runs`/`spans` + JSONL) was
never sampled anyway (it is the billable/audit SoR), and now the full prompt/response I/O is kept for every
run too, not just the debug-worthy ones. **`should_sample_trace()` / `TRACE_SAMPLE_RATE` are deleted.**

| Sink | Sampling | Retention |
|---|---|---|
| **PG `runs`/`spans`** + **JSONL model-call line** | **NEVER sampled** — 100% (spec A2 is absolute for the billable/audit record) | `runs`/`spans`: 90d hot, monthly partitions, drop older (`01 §4.7`, `11 §9`); JSONL: 14–30d (§3) |
| **Langfuse v3 trace payloads** (ClickHouse-backed) | **NONE** — 100% retained; the columnar backend absorbs full trace volume | per-workspace retention policy enforced by Langfuse over its own ClickHouse `langfuse` DB (time-based TTL); PITR-backed, non-critical to serving (`11 §9`) |
| **Prometheus** | n/a (aggregate counters/histograms) | 15–30d; long-term via remote-write if needed (`10 §2`) |

- **Retention story in place of the sample-rate cost bound:** storage is now bounded by **time-based
  retention**, not by dropping payloads. Langfuse owns its `langfuse` ClickHouse DB and ages traces out per
  the per-workspace policy (`10 §2`); operators tune the window if volume demands, but the default is
  keep-everything-for-the-window rather than keep-a-fifth-forever. Cost, tokens, status, latency, and the
  spec-A2 model-call line remain 100% in Postgres regardless — no billing, SLO, or drift-proxy metric was
  ever (or is now) sampled away.

### 8.1 (Phase 2) The `analytics` ClickHouse DB — derived eval/drift + cost rollups

When Postgres rollups strain (`15 §193-197`'s "nightly materialized view" lever), we add our **own
`analytics` DB on the *same* ClickHouse cluster** Langfuse already brought — logically split from Langfuse's
`langfuse` DB (Langfuse owns its schema, untouched; we own `analytics`). It holds eval/drift + cost/usage
rollups **derived from** `runs`/`spans`/`eval_runs`/`eval_results` (Postgres stays authoritative). DDL sketch
(not built in P1):

```sql
-- analytics DB (ClickHouse) — DERIVED, non-authoritative, NO RLS. Illustrative P2 shape.
-- Raw ingest: ReplacingMergeTree gives idempotent re-ingest (dedup on the key), mirroring our
-- per-ref_id eval idempotency and per-run_id run idempotency (10 §9, 05 §7).
CREATE TABLE analytics.run_cost (
  workspace_id   UUID,
  run_id         UUID,            -- dedup key
  agent_id       UUID,
  model          LowCardinality(String),
  day            Date,
  cost_usd       Float64,
  input_tokens   UInt64,
  output_tokens  UInt64,
  ingested_at    DateTime         -- ReplacingMergeTree version col: last write wins on re-ingest
) ENGINE = ReplacingMergeTree(ingested_at)
  ORDER BY (workspace_id, run_id)
  TTL day + INTERVAL {DATA_RETENTION_DAYS} DAY;

CREATE TABLE analytics.eval_result (
  workspace_id UUID, eval_run_id UUID, ref_id String,   -- (eval_run_id, ref_id) dedup key
  agent_id UUID, passed UInt8, score Float64, day Date, ingested_at DateTime
) ENGINE = ReplacingMergeTree(ingested_at)
  ORDER BY (workspace_id, eval_run_id, ref_id)
  TTL day + INTERVAL {DATA_RETENTION_DAYS} DAY;

-- Rollup: AggregatingMergeTree materialized views (Observal's session_stats_agg pattern) pre-aggregate
-- so a dashboard read is a cheap merge, not a full scan.
CREATE MATERIALIZED VIEW analytics.spend_by_ws_day_mv
  ENGINE = AggregatingMergeTree() ORDER BY (workspace_id, day) AS
  SELECT workspace_id, day, sumState(cost_usd) AS cost, countState() AS runs
  FROM analytics.run_cost GROUP BY workspace_id, day;
```

- **Idempotent ingest.** `ReplacingMergeTree` keyed on `run_id` (and `(eval_run_id, ref_id)`) dedups on
  re-ingest — the CH analog of our existing per-`ref_id` job idempotency (`10 §9`). A retried rollup job that
  re-emits a row is a no-op after merge.
- **Rollups are AggregatingMergeTree MVs.** They are exactly where `15 §193-197`'s materialized-view lever
  lands; `/v1/metrics/*` and `/org/spend` read them at scale instead of live Postgres aggregates (`10 §6`).
- **TTL, not sampling,** bounds `analytics` storage: `DATA_RETENTION_DAYS`-style `TTL` on the raw tables (the
  MVs retain their smaller rollups longer as configured).
- **Any CH analytics sink inherits the best-effort `_safe_emit` pattern (§7).** A rollup-emit failure to
  ClickHouse is **category B** — swallowed, counted (`ac_telemetry_emit_failures_total{sink=...}`), never
  propagated into `invoke()` or a run `stop_reason`. ClickHouse is derived; its unavailability degrades
  dashboards, never serving or billing (which read Postgres).
- **Derived / non-authoritative, NO RLS.** Unlike `runs`/`spans` (§9, RLS-backed), the `analytics` DB has no
  row-level security. Tenant isolation is **query-time `workspace_id` filtering**, enforced by a
  `workspace_id`-injecting rollup API, or the DB stays operator-plane only. Billing, quotas, and the publish
  gate **never** read it — they read Postgres `runs.cost_usd`/`eval_runs` (`10 §6`). Scale-out to a
  dedicated/external ClickHouse is a `CLICKHOUSE_URL` config change, not a rewrite (`11 LLD`).

---

## 9. Tenancy & access boundary of observability data

Telemetry is tenant data and obeys the same isolation as everything else (`09`), with store-specific rules the
parent leaves implicit:

- **`runs` / `spans` are `WorkspaceScoped` + RLS** (`01 §6`, `09 §1`). The in-app dashboards (Run explorer,
  Cost, Eval/drift, `10 §10`) read them through `/v1/metrics/*` and `/agents/:id/dashboard` (`08`) under the
  tenant GUC — `set_config('app.workspace_id', :w, true)` (`09 §1.1`) — so a builder sees **only their
  workspace's** runs, spans, and cost. No cross-tenant read exists on this path (`09 §8`).
- **Langfuse has no RLS** (it's a separate self-hosted store). Its UI is **operator-only**. The console
  `runs.trace_id` deep-link (`10 §2`) is therefore gated: a plain builder gets the **in-app** Run explorer
  (RLS-scoped PG + spans); the **raw Langfuse** deep-link is shown only to platform operators / admins
  (`15 §1` admin), never wired to open another tenant's trace.
- **Prometheus + Grafana (Ops/Cost boards, `10 §10`) are operator-only** — they carry a `workspace` label but
  are not a tenant-facing surface; tenants get the in-app, RLS-scoped dashboards instead.
- **(P2) The `analytics` ClickHouse DB has no RLS** (§8.1). It is reached either operator-plane only, or by a
  **`workspace_id`-injecting rollup API** that filters at query time — ClickHouse carries no tenant GUC, so the
  `workspace_id` predicate is the isolation boundary and must be set by the API, never trusted from the caller.
  Like Langfuse, it is derived and never the authoritative tenant/money record (that stays RLS-backed Postgres).
- **Org-plane reporting (`15`) is the one sanctioned cross-tenant read.** It reads `runs.cost_usd` (and
  `eval_runs`, `agent_versions`) via the single `SECURITY DEFINER` function (`15 §2` `org_report_agents()`),
  never Langfuse or Prometheus, and is **metadata-only for private-tier agents** (`15 §2`). All other
  cross-tenant telemetry access is forbidden by the `09 §8` isolation suite.
- **PII/secret redaction (§5) runs before any write that leaves the tenant's Postgres** — i.e. before Langfuse
  and the JSONL line, which have weaker isolation than RLS. The `runs`/`spans` rows keep *tokens/cost/status*,
  not raw I/O, so the lean tenant table carries no payload to leak (`10 §2`).

---

## 10. Alert delivery wiring (ops) vs owner notifications

The SLO table, thresholds, and the Alertmanager `rules.yml` skeleton are in **`10 §5` — not restated.** This
LLD pins only the **delivery split** the parent states in prose (`10 §8`):

- **Ops alerts (`10 §5`)** fire from **Prometheus → Alertmanager** to **platform operators** (PagerDuty page /
  Slack warn), routed by `severity` (`critical`→page, `warning`→Slack). They answer "is the *system* healthy".
  The new `ac_telemetry_emit_failures_total` (§7) gets a `warning` route (observability degraded, serving fine).
- **Owner notifications (`16`)** are **in-product**, written to the `notifications` table (`16 §1`), and go to an
  **agent's owner** (`drift`, `budget_threshold`, `eval_failed`, `model_deprecated`). They answer "is *your
  agent* OK". Delivery, dedup, and severity-by-kind are owned by **`16` — cross-ref, not duplicated here.**
- **A signal can feed both** (distinct audiences/surfaces): workspace spend drives the ops `SpendAnomaly` page
  (`10 §5`) *and* the owner `budget_threshold` notice (`16`); the `model_deprecation_scan` job (`10 §8`) emits an
  owner `model_deprecated` notification (`16 §3`). The *source metric* is shared; the two delivery paths are
  independent, so neither can silently swallow the other.

---

## 11. Verification performed

1. **Snippet compile** — every `python` block via `compile()` (same harness as `00`/`01`/`02`/`03`/`05`/`06`
   LLDs); the one `sql` block checked against PostgreSQL 16 syntax.
2. **Cross-ref** — against the docs read in full: `10` parent (`§1` span helper / `SpanRecord`, `§2` three
   stores, `§3` Langfuse, `§4` metrics, `§5` SLOs/alerts, `§6` cost bridge, `§7` feedback loop, `§8`
   provider-resilience, `§9` jobs, `§10` dashboards); `03 §2` (`invoke`/`_close_run` persist order,
   `AsyncSession`); `02 §7` (`NormalizedUsage` fields, `quantize_usd`/`attach_cost`, cost-from-profiles);
   `05 §5/§7` (verdict/`run_source=eval`/scores); `06 §2` (tier-0 PII+secret redactors reused by §5);
   `00-conv §1` (category-B telemetry) + `§4.2` (persist-then-reconcile atomicity); `01 §4.7` (`runs`/`spans`/
   `trace_id`/`parent_run_id` DDL); `09 §1/§8` (`set_config` GUC, isolation suite); `15 §2` (`SECURITY DEFINER`
   org read, private-tier metadata-only); `16` (owner-notification delivery); `11 §9` (partitioning/retention).
3. **Contract checks** — the spec-A2 line (`ModelCallLog`, §3) carries all seven fields
   (`model, prompt_version, input, output, latency_ms, cost_usd, success`) verbatim (`scope.md §146` /
   `spec.md §115`); `run_source` values (`playground|api|eval|channel|tool_test|subagent`) match `01 §3`
   (task "published" = `api`); cost is sourced from profiles (`02`) and `quantize_usd`-d once, never from
   LiteLLM — identical value in `spans.cost_usd`, Langfuse `total_cost`, and the JSONL line.
4. **Three flagged amendments** — PG spans buffered-and-flushed-with-the-run (not per-span) reconciling
   `10 §1` with `03 §2`/`00-conv §4.2`; `_open_trace`/`_close_trace` + `kind`-split `_emit_langfuse`
   reconciling `10 §3`; the telemetry error taxonomy (`_safe_emit`, category B) making explicit what `10` only
   implies — recorded as callouts, not parent edits (a later reconciliation pass folds them, per the LLD
   `README.md` convention).

→ `10` added. The `⚠️ Amendment` callouts above await the reconciliation pass; index owned by `README.md`.

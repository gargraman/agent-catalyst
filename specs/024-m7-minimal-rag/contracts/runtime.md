# Runtime Contract — 024 Minimal RAG (M7)

The runtime stays a pure library (P6). Everything below is in `services/runtime/` and imports no web framework or DB.

## 1. Injected port (API implements)

```python
class KnowledgeAccess(Protocol):
    async def search(self, ref: RetrievalRef, qvec: list[float], *, top_k: int,
                     threshold: float) -> SearchOutcome: ...
```

- The implementation MUST enforce tenancy (request/tenant session + explicit `workspace_id` filter), the KB's
  **current** access policy for the caller (unless constructed with `bypass_access=True` for eval), KB archived →
  `unavailable`, and the `embedding_model = ref.embedding_model` filter.
- It returns at most `top_k` passages with `score ≥ threshold`, ordered by score desc. It never raises for
  "not entitled": that is `status="unavailable"`. Infrastructure errors MAY raise; the runtime treats them as
  `degraded` for that binding.

`RunContext` additions: `knowledge: KnowledgeAccess | None = None`, `embedding_api_key: str | None = None`.
A snapshot with retrieval bindings but `ctx.knowledge is None` ⇒ every binding `degraded("no_knowledge_access")`
(never a crash, never a silent "used").

## 2. Retrieval step (shared by always-on and tool mode)

```
retrieve(query, refs) →
  embed(query) via gateway.embed (1 call per distinct embedding model; cost → state.embed_calls)
  for ref in refs: outcome = ctx.knowledge.search(ref, qvec, top_k, threshold)
  for passage: scan(passage.text)  → flagged ⇒ withhold + detection record     (Q1 = A)
                                   → scanner raised ⇒ withhold ALL of this retrieval (fail closed)
  merge across refs by score desc → trim to min(ref.max_ctx_tokens) whole passages
  → (rendered context, citations, BindingSummary per ref)
```

- Embedding failure ⇒ bindings `degraded("embedding_failed")`, run continues without context (FR-020).
- P2 config levers present (`hybrid`, `reranker`, `metadata_filters`) ⇒ ignored + one `Substitution` each
  (`action="dropped"`), deduped per run.

## 3. Always-on mode

When at least one binding is `always_on`: retrieval runs **once, before the first model turn**, with
query = the latest user message text. Rendered context (research D13) is inserted as a user-role message
immediately before the latest user message. Then the context-overflow guard and the normal pass-through/validated
modes run unchanged. Pass-through streaming is preserved (retrieval precedes the stream).

## 4. Tool mode

When at least one binding is `tool` and the frozen profile supports tool calling: the request gets the synthetic tool

```json
{"name": "search_knowledge",
 "description": "Search the agent's knowledge bases for passages relevant to a query.",
 "parameters": {"type": "object", "additionalProperties": false, "required": ["query"],
   "properties": {"query": {"type": "string", "minLength": 1, "maxLength": 500},
                  "knowledge_base": {"type": "string", "enum": ["<bound KB names>"]}}}}
```

and runs in validated mode. Per turn:

| Turn content | Runtime action |
|---|---|
| no tool calls / only external tool calls | final, exactly as today (external calls validated + surfaced, never executed) |
| ≥ 1 valid `search_knowledge` | execute each (≤ remaining search budget), append assistant tool-call message + one `tool` result per call (search results or the mixed-turn "not executed" note for external calls), next turn |
| invalid `search_knowledge` args | existing F3 rejection → correction turn (counts against `max_corrections`) |
| search budget (`ctx.max_tool_calls`) exhausted | outcome `BOUNDED_EXIT`, error `{type: "tool_budget_exhausted"}`, usage/cost reported |

Search-turn intermediate text is suppressed from the stream (like correction turns). Every turn is accounted
(usage summed, a `ModelCallRecord` per turn). Profile without tool calling ⇒ treat `tool` bindings as `always_on` +
`Substitution("retrieval_tool","downgraded", …)`.

## 5. Events

| Event | When | Data |
|---|---|---|
| `EventType.CITATIONS` (new) | once, after final `TEXT_DELTA`/`TOOL_CALL_END`, before `USAGE`; only if the snapshot has retrieval bindings | `{knowledge_used: bool, citations: [Citation], knowledge_status: [{kb_id, status}]}` |
| `TOOL_CALL_START` / `TOOL_CALL_END` / `STOP` | now also produced by the real transport from streamed chunks (research D9) | unchanged shapes |

`search_knowledge` calls are **never** surfaced as `TOOL_CALL_END` to the client (they're platform-internal); they are
visible in the run's retrieval summary (`searches`).

## 6. Run record and per-call records

- `RunRecord.retrieval: RetrievalSummary | None` (data-model §6); `RunRecord.cost_usd = Σ turn costs + Σ embedding
  costs` (Decimal); `cost_is_estimate` true if any part is.
- One `ModelCallRecord(role="embedding", output_text="", input_text=<query>)` per query-embedding call; the 023 sink
  writes it with the unchanged key set.
- `InvokeResult.citations` equals the `CITATIONS` event payload's citations (entry-point parity).

## 7. Injection scanner

```python
SCANNER_VERSION = "inj-v1"
def scan(text: str) -> ScanResult  # pure, deterministic, no I/O; raises only on programmer error
CATEGORIES = ("instruction_override", "role_impersonation", "tool_hijack", "exfiltration", "hidden_instruction")
```

Detection record (emitted to `ctx.record_detection` if set, else counted only):
`{run_id, workspace_id, kb_id, document_id, chunk_id, categories, checkpoint: "retrieve", scanner_version}`. No text.

## 8. Gateway embedding

```python
async def embed(texts: list[str], *, provider: str, model: str, api_key: str,
                timeout: float | None = None) -> EmbeddingResult
```

Resolves the embedding profile (fail closed on unknown/chat-only model: `FileNotFoundError` → caller config error),
batches by `max_batch`, computes cost from our profile (`input` rate only), quantizes once, normalizes transport errors
to a typed `EmbeddingError(type, retryable)` with the key scrubbed. Vector length must equal the profile `dimension`,
else `EmbeddingError("dimension_mismatch")`.

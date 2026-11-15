"""The runtime retrieval step (feature 024, contracts/runtime.md §2; spec FR-012..FR-020).

`retrieve(query, refs, ctx)`:
  1. embed the query once per distinct (provider, embedding model) through `gateway.embed` (our cost accounting);
  2. search each bound KB through the injected `ctx.knowledge` port with that binding's top_k / threshold;
  3. merge by score, keep whole passages within the SMALLEST max_ctx_tokens among the bindings;
  4. render the delimited context + citations, and an honest per-binding status.
Nothing here raises for a retrieval problem: no port, missing key, embedding failure, search failure, denied/archived
KB — each binding degrades with a reason and the run proceeds without that knowledge (FR-020). P2 levers (hybrid,
reranker, metadata filters) are accepted in config but not executed in P1: one `Substitution(..., "dropped")` each.
Pure: no DB, no web.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, replace

from runtime.guardrails import injection
from runtime.providers import gateway
from runtime.providers.profiles import Substitution
from runtime.retrieval.render import render_context
from runtime.run import BindingSummary, Citation

_DEFAULT_TOP_K = 6
_DEFAULT_THRESHOLD = 0.2
_DEFAULT_MAX_CTX = 2000
_P2_LEVERS = (("hybrid", "retrieval_hybrid"), ("reranker", "retrieval_reranker"),
              ("metadata_filters", "retrieval_metadata_filters"))
_KEYLESS_PROVIDERS = frozenset({"bedrock"})       # AWS credential chain, no per-workspace key (decision 2026-11-14)


@dataclass(frozen=True)
class RetrievalStepResult:
    rendered: str
    passages_used: tuple
    citations: tuple
    summaries: tuple
    substitutions: tuple
    embeddings: tuple
    searches: int = 0
    kb_names: tuple = ()          # ((kb_id, name), …) — kept so `shrink` can re-render
    embedding_calls: tuple = ()   # ((provider, model, latency_ms), …) aligned with `embeddings` — per-call records
    detections: tuple = ()        # US3: one dict per withheld passage — ids + categories only, never text


def _cfg(ref) -> dict:
    return ref.retrieval_config or {}


def _levers(refs) -> tuple:
    seen, subs = set(), []
    for ref in refs:
        cfg = _cfg(ref)
        for key, feature in _P2_LEVERS:
            if cfg.get(key) and feature not in seen:
                seen.add(feature)
                subs.append(Substitution(feature, "dropped", f"{key} is not executed in P1; plain vector search used"))
    return tuple(subs)


def _citations(passages) -> tuple:
    return tuple(Citation(id=f"c{n}", kb_id=p.kb_id, document_id=p.document_id, document_name=p.document_name,
                          chunk_id=p.chunk_id, score=float(p.score), page=(p.meta or {}).get("page"),
                          heading=(p.meta or {}).get("heading"))
                 for n, p in enumerate(passages, start=1))


def _degraded(refs, reason: str) -> tuple:
    return tuple(BindingSummary(kb_id=r.kb_id, kb_index_version=r.kb_index_version, status="degraded",
                                degraded_reason=reason) for r in refs)


async def retrieve(query: str, refs, ctx, *, sink: list | None = None) -> RetrievalStepResult:
    """`sink` receives (EmbeddingResult, call) as each embedding completes, so a caller that cancels us on its
    deadline can still account for embeddings already billed."""
    refs = tuple(refs)
    subs = _levers(refs)
    names = tuple((r.kb_id, r.kb_name or r.kb_id) for r in refs)
    if ctx.knowledge is None:
        return RetrievalStepResult("", (), (), _degraded(refs, "no_knowledge_access"), subs, (), 0, names)
    calls = []

    # 1. one embedding per distinct (provider, model)
    vectors: dict[tuple[str, str], list[float]] = {}
    failed: dict[tuple[str, str], str] = {}
    embeddings = []
    for key in dict.fromkeys((r.embedding_provider, r.embedding_model) for r in refs):
        provider, model = key
        keyless = provider in _KEYLESS_PROVIDERS
        if not keyless and not ctx.embedding_api_key:
            failed[key] = "embedding_key_missing"
            continue
        t0 = time.perf_counter()
        try:
            res = await gateway.embed([query], provider=provider, model=model,
                                      api_key=None if keyless else ctx.embedding_api_key,
                                      region=ctx.embedding_region if keyless else None)
            embeddings.append(res)
            calls.append((provider, model, int((time.perf_counter() - t0) * 1000)))
            if sink is not None:
                sink.append((res, calls[-1]))
            vectors[key] = list(res.vectors[0])
        except Exception:  # noqa: BLE001 — EmbeddingError, unknown profile, anything: degrade, never fail the run
            failed[key] = "embedding_failed"

    # 2. search each binding
    summaries: dict[str, BindingSummary] = {}
    candidates = []
    detections: list[dict] = []
    scan_failed = False
    searches = 0
    for ref in refs:
        key = (ref.embedding_provider, ref.embedding_model)
        if key in failed:
            summaries[ref.kb_id] = BindingSummary(ref.kb_id, ref.kb_index_version, "degraded",
                                                  degraded_reason=failed[key])
            continue
        cfg = _cfg(ref)
        searches += 1
        try:
            out = await ctx.knowledge.search(ref, vectors[key], top_k=int(cfg.get("top_k", _DEFAULT_TOP_K)),
                                             threshold=float(cfg.get("similarity_threshold", _DEFAULT_THRESHOLD)))
        except Exception:  # noqa: BLE001
            summaries[ref.kb_id] = BindingSummary(ref.kb_id, ref.kb_index_version, "degraded",
                                                  degraded_reason="search_failed")
            continue
        if out.status != "ok":
            summaries[ref.kb_id] = BindingSummary(ref.kb_id, ref.kb_index_version, "unavailable")
            continue
        if not out.passages:
            summaries[ref.kb_id] = BindingSummary(ref.kb_id, ref.kb_index_version, "no_match")
            continue
        # US3 (research D11): scan EVERY passage before merge/trim; flagged → withheld + detection (no text).
        clean = []
        try:
            for p in out.passages:
                res = injection.scan(p.text)
                if res.flagged:
                    detections.append({"kb_id": p.kb_id, "document_id": p.document_id, "chunk_id": p.chunk_id,
                                       "categories": list(res.categories), "checkpoint": "retrieve",
                                       "scanner_version": res.scanner_version})
                else:
                    clean.append(p)
        except Exception:  # noqa: BLE001 — scanner failure: fail CLOSED for the whole retrieval (FR-024)
            scan_failed = True
            break
        withheld = len(out.passages) - len(clean)
        if not clean:
            summaries[ref.kb_id] = BindingSummary(ref.kb_id, ref.kb_index_version, "degraded",
                                                  passages_withheld=withheld, degraded_reason="injection_withheld")
            continue
        summaries[ref.kb_id] = BindingSummary(ref.kb_id, ref.kb_index_version, "used", passages_withheld=withheld,
                                              top_score=max(float(p.score) for p in clean))
        candidates.extend(clean)

    if scan_failed:                       # nothing unscanned may reach the model
        candidates, detections = [], []
        for r in refs:                    # bindings that had (or would have had) passages → degraded("scan_failed")
            prev = summaries.get(r.kb_id)
            if prev is None or prev.status == "used" or prev.degraded_reason == "injection_withheld":
                summaries[r.kb_id] = BindingSummary(r.kb_id, r.kb_index_version, "degraded",
                                                    degraded_reason="scan_failed")

    # 3. merge by score, whole passages within the smallest budget
    budget = min((int(_cfg(r).get("max_ctx_tokens", _DEFAULT_MAX_CTX)) for r in refs), default=_DEFAULT_MAX_CTX)
    used, spent = [], 0
    for p in sorted(candidates, key=lambda p: -float(p.score)):
        if spent + p.token_count <= budget:
            used.append(p)
            spent += p.token_count
    result = RetrievalStepResult("", (), (), tuple(summaries[r.kb_id] for r in refs), subs, tuple(embeddings),
                                 searches, names, tuple(calls), tuple(detections))
    return shrink(result, len(used), passages=tuple(used))


def shrink(result: RetrievalStepResult, keep: int, *, passages: tuple | None = None) -> RetrievalStepResult:
    """Keep only the top `keep` passages (score order), re-render, renumber citations and recount per-binding usage.
    A binding that had matches but lost all of them to the budget is reported degraded("context_trimmed")."""
    pool = passages if passages is not None else result.passages_used
    kept = tuple(pool[:max(0, keep)])
    counts: dict[str, int] = {}
    for p in kept:
        counts[p.kb_id] = counts.get(p.kb_id, 0) + 1
    summaries = []
    for s in result.summaries:
        if s.status == "used" or s.degraded_reason == "context_trimmed":
            n = counts.get(s.kb_id, 0)
            summaries.append(replace(s, status="used", passages_used=n, degraded_reason=None) if n
                             else replace(s, status="degraded", passages_used=0, degraded_reason="context_trimmed"))
        else:
            summaries.append(s)
    return replace(result, rendered=render_context(kept, dict(result.kb_names)), passages_used=kept,
                   citations=_citations(kept), summaries=tuple(summaries))

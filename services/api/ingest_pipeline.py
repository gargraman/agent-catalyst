"""Ingestion pipeline core (feature 024, research D4/D5; spec FR-004..FR-007).

`process_job` orchestrates ONE ingestion job against an injected repository, storage and embedder so the control flow
is unit-testable without a DB (tests/test_ingest_pipeline.py) and the DB effects are proven end-to-end separately
(tests/test_api_ingestion.py):

    load → mark_processing → dedup check → storage.get → extract → chunk → batched embed (heartbeat + progress per
    batch, chunks written at generation = active + 1) → finalize_ready (ONE transaction in the repo: flip
    active_generation, drop the previous generation, bump KB index_version)

Any failure deletes the half-written generation and finalizes as failed; the previous generation stays live (FR-005).
Error payloads are FIXED client-safe strings — never exception text — so neither a provider-echoed API key nor the
internal storage key can leak into a job row or response.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Awaitable, Callable, Protocol

from ingestion.chunk import ChunkConfig, chunk
from ingestion.extract import ExtractError, extract
from runtime.guardrails import injection
from runtime.providers.ir import EmbeddingError, EmbeddingResult

log = logging.getLogger("ac.ingest")


@dataclass(frozen=True)
class JobContext:
    job_id: str
    workspace_id: str
    kb_id: str
    document_id: str
    kind: str
    storage_key: str
    media_type: str
    content_hash: str
    active_generation: int
    dedup_key: str | None
    embedding_model: str
    chunk_config: dict


@dataclass(frozen=True)
class ChunkRow:
    ordinal: int
    text: str
    token_count: int
    embedding: tuple[float, ...]
    embedding_model: str
    meta: dict = field(default_factory=dict)
    injection_categories: list = field(default_factory=list)    # US3 ingest-time scan (flag, not refusal)


def _scan_categories(text: str) -> list[str]:
    """Ingest-time scan (research D11). Informational: a scanner error records no flag rather than failing the
    upload — the retrieval-time scan is the enforcement point and fails CLOSED."""
    try:
        return list(injection.scan(text).categories)
    except Exception:  # noqa: BLE001
        return []


class IngestRepo(Protocol):
    async def load(self, job_id) -> JobContext: ...
    async def mark_processing(self, job_id) -> bool | None: ...   # False ⇒ not claimable
    async def set_progress(self, job_id, done: int, total: int) -> None: ...
    async def insert_chunks(self, document_id, generation: int, rows: list[ChunkRow]) -> None: ...
    async def delete_generation(self, document_id, generation: int) -> None: ...
    async def finalize_ready(self, job_id, *, generation: int, previous_generation: int, chunk_count: int,
                             dedup_key: str, cost_usd: Decimal, cost_is_estimate: bool,
                             injection_flags: dict | None = None) -> None: ...
    async def finalize_skipped(self, job_id) -> None: ...
    async def finalize_failed(self, job_id, *, error: dict, has_live_generation: bool,
                              cost_usd: Decimal = Decimal(0), cost_is_estimate: bool = False) -> None: ...


Embedder = Callable[[list[str]], Awaitable[EmbeddingResult]]


def dedup_key_for(content_hash: str, embedding_model: str, cfg: ChunkConfig) -> str:
    """FR-006: same bytes + same model + same (normalized) chunking ⇒ nothing to redo."""
    canon = json.dumps(cfg.canonical(), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((content_hash + embedding_model + canon).encode()).hexdigest()


def _err(code: str, message: str, retryable: bool) -> dict:
    return {"code": code, "message": message, "retryable": retryable}


async def process_job(job_id, *, repo: IngestRepo, storage, embedder: Embedder,
                      heartbeat: Callable[[], Awaitable[None]], batch_size: int = 100) -> None:
    ctx = await repo.load(job_id)
    if await repo.mark_processing(job_id) is False:     # reaped / superseded since it was queued: do nothing
        return
    new_gen = ctx.active_generation + 1
    wrote_chunks = False
    spent = _Spend()
    try:
        cfg = ChunkConfig.from_dict(ctx.chunk_config)
        key = dedup_key_for(ctx.content_hash, ctx.embedding_model, cfg)
        if ctx.active_generation > 0 and ctx.dedup_key == key:
            await repo.finalize_skipped(job_id)
            return

        try:
            data = await storage.get(ctx.storage_key)
        except Exception:  # noqa: BLE001 — never echo the storage error (it names the key)
            raise _Fail(_err("storage_failed", "the stored file could not be read; retry", True)) from None

        try:
            drafts = await asyncio.to_thread(_extract_and_chunk, ctx.media_type, data, cfg)   # CPU off the loop
        except ExtractError as exc:
            raise _Fail(_err(exc.code, exc.message or exc.code, False)) from None
        if not drafts:
            raise _Fail(_err("no_extractable_text", "no text could be extracted", False))

        total = len(drafts)
        await repo.set_progress(job_id, 0, total)
        cost = Decimal(0)
        estimate = False
        flags: dict[str, int] = {}
        done = 0
        size = max(1, batch_size)
        for start in range(0, total, size):
            batch = drafts[start:start + size]
            try:
                res = await embedder([d.text for d in batch])
            except EmbeddingError as exc:
                if exc.type == "provider_unavailable":   # 025 FR-012: breaker open — typed, retryable, no chunks
                    raise _Fail(_err("provider_unavailable", "embedding provider temporarily unavailable (circuit open)",
                                     True)) from None
                raise _Fail(_err("embedding_failed", f"embedding provider call failed ({exc.type})",
                                 bool(exc.retryable))) from None
            except FileNotFoundError:
                raise _Fail(_err("embedding_failed", "the knowledge base's embedding model is not supported", False)) from None
            if len(res.vectors) != len(batch):
                raise _Fail(_err("embedding_failed", "embedding provider returned the wrong number of vectors", True))
            rows = [ChunkRow(ordinal=d.ordinal, text=d.text, token_count=d.token_count, embedding=tuple(v),
                             embedding_model=ctx.embedding_model, meta=dict(d.meta),
                             injection_categories=_scan_categories(d.text))
                    for d, v in zip(batch, res.vectors)]
            for r in rows:
                for c in r.injection_categories:
                    flags[c] = flags.get(c, 0) + 1
            try:
                await repo.insert_chunks(ctx.document_id, new_gen, rows)
            except ChunkDimMismatch:               # 025 US4: re-trying cannot fix a KB/model dimension mismatch
                raise _Fail(_err("chunk_dim_mismatch",
                                 "embedding dimension does not match the knowledge base's declared dimension",
                                 False)) from None
            wrote_chunks = True
            cost += Decimal(res.cost_usd)
            estimate = estimate or res.cost_is_estimate
            spent.cost, spent.estimate = cost, estimate
            done += len(batch)
            await repo.set_progress(job_id, done, total)
            await heartbeat()

        await repo.finalize_ready(job_id, generation=new_gen, previous_generation=ctx.active_generation,
                                  chunk_count=total, dedup_key=key, cost_usd=cost, cost_is_estimate=estimate,
                                  injection_flags=flags)
        from api import metrics                       # counted only once the document is actually live
        for c, n in flags.items():
            metrics.record_injection_detection("ingest", c, n)
    except _Fail as f:
        await _fail(repo, job_id, ctx, new_gen, wrote_chunks, f.error, spent)
    except ValueError:                       # invalid stored chunk_config
        await _fail(repo, job_id, ctx, new_gen, wrote_chunks,
                    _err("config_invalid", "the knowledge base chunk configuration is invalid", False), spent)
    except Exception:  # noqa: BLE001 — a bug must still leave an honest, retryable failed job (never stuck 'processing')
        log.exception("ingestion job crashed", extra={"job_id": str(job_id)})
        await _fail(repo, job_id, ctx, new_gen, wrote_chunks,
                    _err("internal_error", "ingestion failed unexpectedly; retry", True), spent)


class ChunkDimMismatch(Exception):
    """Raised by a repo when the store refuses chunk vectors whose dimension ≠ the KB's declared one (0015 trigger)."""


class _Fail(Exception):
    def __init__(self, error: dict):
        super().__init__(error["code"])
        self.error = error


@dataclass
class _Spend:
    cost: Decimal = Decimal(0)
    estimate: bool = False


def _extract_and_chunk(media_type: str, data: bytes, cfg: ChunkConfig):
    return chunk(extract(media_type, data), cfg)


async def _fail(repo, job_id, ctx: JobContext, new_gen: int, wrote_chunks: bool, error: dict,
                spent: "_Spend | None" = None) -> None:
    if wrote_chunks:
        await repo.delete_generation(ctx.document_id, new_gen)
    spent = spent or _Spend()
    await repo.finalize_failed(job_id, error=error, has_live_generation=ctx.active_generation > 0,
                               cost_usd=spent.cost, cost_is_estimate=spent.estimate)

"""Ingestion pipeline core (feature 024, tasks.md T021; research D4/D5; spec FR-004..FR-007).

`api.ingest_pipeline.process_job(job_id, *, repo, storage, embedder, heartbeat, batch_size)` orchestrates
load → storage.get → extract → chunk → dedup check → batched embed (heartbeat + progress per batch) → write chunks at
generation = active_generation + 1 → ONE atomic finalize (flip generation, drop old one, bump KB index_version) — or, on
any failure, delete the half-written generation and finalize as failed (previous generation stays live).

This test pins the pipeline's contract with its repository (the DB-backed repo is exercised end-to-end by
test_api_ingestion.py). Pure: fake repo/storage/embedder, no DB, no network.

Dedup key contract (FR-006): sha256(content_hash + embedding_model + canonical_chunk_config) where
canonical_chunk_config = compact sorted-key JSON of ChunkConfig.from_dict(kb.chunk_config) as
{"overlap", "size", "structure_aware"} — so `{}` and the explicit defaults dedup identically.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

import pytest

from api.ingest_pipeline import ChunkRow, JobContext, process_job
from runtime.providers.ir import EmbeddingError, EmbeddingResult, NormalizedUsage

DOCS = Path(__file__).parent / "fixtures" / "docs"
_KEY = "sk-live-SECRET-999"
_STORAGE_KEY = "ws/w1/kb/k1/doc/d1/0123456789abcdef"


def _dedup(content_hash: str, model: str, cfg: dict) -> str:
    canon = json.dumps({"overlap": cfg.get("overlap", 120), "size": cfg.get("size", 800),
                        "structure_aware": cfg.get("structure_aware", True)}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((content_hash + model + canon).encode()).hexdigest()


@dataclass
class FakeRepo:
    ctx: JobContext
    calls: list = field(default_factory=list)
    inserted: dict = field(default_factory=dict)       # generation -> [ChunkRow]

    async def load(self, job_id):
        self.calls.append(("load", job_id))
        return self.ctx

    async def mark_processing(self, job_id):
        self.calls.append(("mark_processing", job_id))

    async def set_progress(self, job_id, done, total):
        self.calls.append(("set_progress", done, total))

    async def insert_chunks(self, document_id, generation, rows):
        assert all(isinstance(r, ChunkRow) for r in rows)
        self.inserted.setdefault(generation, []).extend(rows)
        self.calls.append(("insert_chunks", generation, len(rows)))

    async def delete_generation(self, document_id, generation):
        self.inserted.pop(generation, None)
        self.calls.append(("delete_generation", generation))

    async def finalize_ready(self, job_id, **kw):
        self.calls.append(("finalize_ready", kw))

    async def finalize_skipped(self, job_id):
        self.calls.append(("finalize_skipped", job_id))

    async def finalize_failed(self, job_id, **kw):
        self.calls.append(("finalize_failed", kw))

    def last(self, name):
        hits = [c for c in self.calls if c[0] == name]
        return hits[-1] if hits else None


class FakeStorage:
    def __init__(self, objects: dict[str, bytes] | None = None, fail: bool = False):
        self.objects = objects or {}
        self.fail = fail

    async def get(self, key):
        if self.fail:
            raise ConnectionError(f"s3 unreachable while reading {key}")
        return self.objects[key]


class FakeEmbedder:
    """Deterministic 1024-dim vectors; optional failure on the Nth batch call (1-based)."""

    def __init__(self, fail_on_call: int | None = None, tokens_per_text: int = 1000):
        self.calls: list[list[str]] = []
        self.fail_on_call = fail_on_call
        self.tokens_per_text = tokens_per_text

    async def __call__(self, texts):
        self.calls.append(list(texts))
        if self.fail_on_call is not None and len(self.calls) == self.fail_on_call:
            raise EmbeddingError(f"rate limited for {_KEY}".replace(_KEY, "***"), type="provider_error", retryable=True)
        toks = self.tokens_per_text * len(texts)
        return EmbeddingResult(
            vectors=tuple(tuple([0.001 * (i + 1)] * 1024) for i in range(len(texts))),
            usage=NormalizedUsage(toks, 0),
            cost_usd=Decimal(toks) * Decimal("0.02") / Decimal(1_000_000),
            cost_is_estimate=True,
        )


def _ctx(*, data_name="handbook.md", media="text/markdown", active_generation=0, dedup_key=None,
         chunk_config=None, content_hash=None) -> tuple[JobContext, bytes]:
    data = (DOCS / data_name).read_bytes()
    ch = content_hash or hashlib.sha256(data).hexdigest()
    return JobContext(
        job_id="j1", workspace_id="w1", kb_id="k1", document_id="d1", kind="upload",
        storage_key=_STORAGE_KEY, media_type=media, content_hash=ch,
        active_generation=active_generation, dedup_key=dedup_key,
        embedding_model="text-embedding-3-small", chunk_config=chunk_config if chunk_config is not None else {},
    ), data


async def _run(ctx, data, *, embedder=None, storage=None, batch_size=3):
    repo = FakeRepo(ctx)
    beats = []

    async def heartbeat():
        beats.append(1)

    emb = embedder or FakeEmbedder()
    st = storage or FakeStorage({_STORAGE_KEY: data})
    await process_job("j1", repo=repo, storage=st, embedder=emb, heartbeat=heartbeat, batch_size=batch_size)
    return repo, emb, beats


# ------------------------------------------------------------------------------------------------- happy path


async def test_happy_path_writes_next_generation_then_finalizes_atomically():
    ctx, data = _ctx(active_generation=2)
    repo, emb, beats = await _run(ctx, data, batch_size=2)
    assert repo.calls[0] == ("load", "j1") and repo.calls[1] == ("mark_processing", "j1")
    assert set(repo.inserted) == {3}, "chunks must be written at active_generation + 1"
    rows = repo.inserted[3]
    n = len(rows)
    assert n >= 3 and [r.ordinal for r in rows] == list(range(n))
    assert all(len(r.embedding) == 1024 and r.embedding_model == "text-embedding-3-small" for r in rows)
    assert all(r.text and r.token_count > 0 and "heading" in r.meta for r in rows)
    assert len(emb.calls) == -(-n // 2)                                  # ceil(n / batch_size)
    assert len(beats) >= len(emb.calls)                                  # heartbeat at least once per batch
    progress = [c for c in repo.calls if c[0] == "set_progress"]
    assert progress[-1] == ("set_progress", n, n)
    fin = repo.last("finalize_ready")[1]
    assert fin["generation"] == 3 and fin["chunk_count"] == n
    assert fin["previous_generation"] == 2                               # repo drops it inside the same txn
    assert fin["cost_usd"] == sum((Decimal(1000 * len(b)) * Decimal("0.02") / Decimal(10**6) for b in emb.calls),
                                  Decimal(0))
    assert fin["cost_is_estimate"] is True
    assert fin["dedup_key"] == _dedup(ctx.content_hash, ctx.embedding_model, {})
    assert repo.last("finalize_failed") is None and repo.last("delete_generation") is None


async def test_handbook_fact_is_in_some_chunk_with_its_heading():
    ctx, data = _ctx()
    repo, _, _ = await _run(ctx, data)
    hits = [r for r in repo.inserted[1] if "Annual leave is 27 days" in r.text]
    assert hits and hits[0].meta["heading"] == "Leave Policy"


async def test_kb_chunk_config_is_honoured():
    ctx, data = _ctx(chunk_config={"size": 150, "overlap": 0})
    repo, _, _ = await _run(ctx, data)
    assert all(r.token_count <= 150 for r in repo.inserted[1])
    ctx2, _ = _ctx()
    repo2, _, _ = await _run(ctx2, data)
    assert len(repo.inserted[1]) > len(repo2.inserted[1])               # smaller size ⇒ more chunks


# ------------------------------------------------------------------------------------------------- failures


async def test_embedding_failure_on_batch_3_rolls_back_new_generation_keeps_previous():
    ctx, data = _ctx(active_generation=1)
    repo, emb, _ = await _run(ctx, data, embedder=FakeEmbedder(fail_on_call=3), batch_size=1)
    assert len(emb.calls) == 3
    assert repo.last("delete_generation") == ("delete_generation", 2)
    assert 2 not in repo.inserted
    fail = repo.last("finalize_failed")[1]
    assert fail["error"]["code"] == "embedding_failed" and fail["error"]["retryable"] is True
    assert fail["has_live_generation"] is True                           # repo returns the doc to 'ready'
    assert repo.last("finalize_ready") is None


async def test_first_upload_failure_has_no_live_generation():
    ctx, data = _ctx(active_generation=0)
    repo, _, _ = await _run(ctx, data, embedder=FakeEmbedder(fail_on_call=1), batch_size=1)
    assert repo.last("finalize_failed")[1]["has_live_generation"] is False   # repo marks the doc 'failed'


@pytest.mark.parametrize("name, media, code", [
    ("scanned.pdf", "application/pdf", "no_extractable_text"),
    ("empty.txt", "text/plain", "no_extractable_text"),
])
async def test_extract_failure_is_non_retryable_and_never_embeds(name, media, code):
    ctx, data = _ctx(data_name=name, media=media, content_hash="x" * 64)
    repo, emb, _ = await _run(ctx, data)
    assert emb.calls == []
    err = repo.last("finalize_failed")[1]["error"]
    assert err["code"] == code and err["retryable"] is False
    assert repo.inserted == {}


async def test_corrupt_pdf_is_parse_failed():
    ctx, _ = _ctx(data_name="policy.pdf", media="application/pdf")
    repo, emb, _ = await _run(ctx, b"%PDF-1.4\nnot really a pdf\n%%EOF")
    assert emb.calls == [] and repo.last("finalize_failed")[1]["error"]["code"] == "parse_failed"


async def test_storage_failure_is_retryable_and_never_leaks_storage_key():
    ctx, data = _ctx()
    repo, emb, _ = await _run(ctx, data, storage=FakeStorage(fail=True))
    err = repo.last("finalize_failed")[1]["error"]
    assert err["code"] == "storage_failed" and err["retryable"] is True
    assert _STORAGE_KEY not in json.dumps(err) and "0123456789abcdef" not in json.dumps(err)
    assert emb.calls == []


async def test_error_messages_never_contain_the_api_key():
    ctx, data = _ctx()

    class LeakyEmbedder(FakeEmbedder):
        async def __call__(self, texts):
            raise EmbeddingError(f"401 invalid key {_KEY}", type="provider_error", retryable=False)

    repo, _, _ = await _run(ctx, data, embedder=LeakyEmbedder())
    err = repo.last("finalize_failed")[1]["error"]
    assert _KEY not in json.dumps(err)
    assert err["code"] == "embedding_failed" and err["retryable"] is False


# ------------------------------------------------------------------------------------------------- dedup


async def test_dedup_unchanged_content_skips_embedding_and_costs_nothing():
    ctx0, data = _ctx()
    key = _dedup(ctx0.content_hash, "text-embedding-3-small", {})
    ctx, _ = _ctx(active_generation=4, dedup_key=key)
    repo, emb, _ = await _run(ctx, data)
    assert emb.calls == [] and repo.inserted == {}
    assert repo.last("finalize_skipped") == ("finalize_skipped", "j1")
    assert repo.last("finalize_ready") is None


async def test_dedup_treats_empty_and_explicit_default_chunk_config_alike():
    ctx0, data = _ctx()
    key = _dedup(ctx0.content_hash, "text-embedding-3-small", {"size": 800, "overlap": 120, "structure_aware": True})
    ctx, _ = _ctx(active_generation=1, dedup_key=key, chunk_config={})
    repo, emb, _ = await _run(ctx, data)
    assert emb.calls == [] and repo.last("finalize_skipped") is not None


@pytest.mark.parametrize("change", ["model", "config", "no_live_generation"])
async def test_dedup_does_not_skip_when_anything_changed(change):
    ctx0, data = _ctx()
    key = _dedup(ctx0.content_hash, "text-embedding-3-small", {})
    if change == "model":
        key = _dedup(ctx0.content_hash, "another-embedding-model", {})
        ctx, _ = _ctx(active_generation=1, dedup_key=key)
    elif change == "config":
        ctx, _ = _ctx(active_generation=1, dedup_key=key, chunk_config={"size": 400, "overlap": 40})
    else:
        ctx, _ = _ctx(active_generation=0, dedup_key=key)           # same key but nothing live: must ingest
    repo, emb, _ = await _run(ctx, data)
    assert emb.calls and repo.last("finalize_ready") is not None and repo.last("finalize_skipped") is None


# ------------------------------------------------------------------------------------------------- injection scan (US3)

@pytest.fixture
def fake_scanner(monkeypatch):
    """T054: deterministic fake (flags "POISON") so this tests the ingest WIRING; patterns live in
    test_injection_scanner.py. ingest_pipeline must call it as `injection.scan(...)` (module attribute)."""
    from runtime.guardrails import injection
    monkeypatch.setattr(injection, "scan", lambda text: injection.ScanResult(True, ("instruction_override", "tool_hijack"))
                        if "POISON" in text else injection.ScanResult(False, ()))


async def test_ingest_scan_flags_chunks_and_document_but_stays_ready(fake_scanner):
    from api import metrics
    before = metrics.REGISTRY.get_sample_value("ac_injection_detections_total",
                                               {"checkpoint": "ingest", "category": "tool_hijack"}) or 0.0
    ctx, data = _ctx(data_name="poisoned.md")
    repo, _, _ = await _run(ctx, data)
    rows = repo.inserted[1]
    flagged = [r for r in rows if r.injection_categories]
    assert flagged and all("POISON" in r.text for r in flagged)
    assert all(r.injection_categories == ["instruction_override", "tool_hijack"] for r in flagged)
    assert all(r.injection_categories == [] for r in rows if "POISON" not in r.text)
    kw = repo.last("finalize_ready")[1]                          # flag != refusal: still finalizes ready
    assert kw["injection_flags"] == {"instruction_override": len(flagged), "tool_hijack": len(flagged)}
    assert repo.last("finalize_failed") is None
    after = metrics.REGISTRY.get_sample_value("ac_injection_detections_total",
                                              {"checkpoint": "ingest", "category": "tool_hijack"})
    assert after == before + len(flagged)


async def test_clean_document_has_empty_flags(fake_scanner):
    ctx, data = _ctx()
    repo, _, _ = await _run(ctx, data)
    assert repo.last("finalize_ready")[1]["injection_flags"] == {}
    assert all(r.injection_categories == [] for r in repo.inserted[1])


# ------------------------------------------------------------------------------------------------- code review 2026-11-15

async def test_partial_failure_records_the_embedding_cost_already_incurred():
    """#4: batches 1–2 were billed before batch 3 failed — that spend must reach the job row (and the spend cap)."""
    ctx, data = _ctx()
    repo, emb, _ = await _run(ctx, data, embedder=FakeEmbedder(fail_on_call=3), batch_size=1)
    kw = repo.last("finalize_failed")[1]
    billed = Decimal(2 * 1000) * Decimal("0.02") / Decimal(1_000_000)
    assert kw["cost_usd"] == billed and kw["cost_is_estimate"] is True


async def test_job_not_claimable_is_a_no_op():
    """#2: mark_processing returning False (job reaped / superseded) → the pipeline does nothing at all."""
    ctx, data = _ctx()
    repo = FakeRepo(ctx)

    async def _refuse(job_id):
        repo.calls.append(("mark_processing", job_id))
        return False
    repo.mark_processing = _refuse
    emb = FakeEmbedder()
    await process_job("j1", repo=repo, storage=FakeStorage({_STORAGE_KEY: data}), embedder=emb,
                      heartbeat=lambda: None, batch_size=3)
    assert emb.calls == [] and not repo.inserted
    assert repo.last("finalize_ready") is None and repo.last("finalize_failed") is None


async def test_extract_and_chunk_run_off_the_event_loop(monkeypatch):
    """#7: pypdf/python-docx parsing is CPU work — it must not stall every request and the heartbeat ticker."""
    import asyncio
    from api import ingest_pipeline
    seen = []
    real = asyncio.to_thread

    async def _spy(fn, *a, **k):
        seen.append(fn.__name__)
        return await real(fn, *a, **k)
    monkeypatch.setattr(ingest_pipeline.asyncio, "to_thread", _spy)
    ctx, data = _ctx()
    await _run(ctx, data)
    assert "_extract_and_chunk" in seen

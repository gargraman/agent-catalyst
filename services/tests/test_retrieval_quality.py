"""Retrieval quality on known questions (feature 024, tasks.md T041/T050; spec SC-001; analyze C1 / constitution §4).

Real extraction + chunking of the fixture documents, a deterministic FAKE embedder (hashed bag-of-words → 1024-dim unit
vector) and an in-memory cosine kNN `KnowledgeAccess`, driven through the real `runtime.retrieval.step.retrieve`.

Two question sets: `known_questions.dev.v1.jsonl` (tuning — chunking/render may be adjusted against it) and
`known_questions.heldout.v1.jsonl` (measurement — written BEFORE any tuning, never consulted while tuning; its sha256 is
recorded in research.md D8). The SC-001 gate asserts on the HELD-OUT set only; dev is printed as "tuning".

Design choice made before any result (not tuning): the similarity threshold is 0.0 here. The 0.2 default is calibrated
for real embedding models; a hashed bag-of-words has a different cosine scale, so applying it would measure the fake
embedder rather than the retrieval pipeline. top_k and max_ctx_tokens stay at their defaults (6 / 2000).
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal
from pathlib import Path

import pytest

from ingestion.chunk import ChunkConfig, chunk
from ingestion.extract import extract, sniff
from runtime.providers import gateway
from runtime.providers.ir import EmbeddingResult, NormalizedUsage
from runtime.retrieval.port import Passage, SearchOutcome
from runtime.retrieval.step import retrieve
from runtime.snapshot import RetrievalRef
from tests._invoke_helpers import make_context

HERE = Path(__file__).parent
DOCS = HERE / "fixtures" / "docs"
QSETS = HERE / "fixtures" / "retrieval"
_DOCS = ["handbook.md", "faq.txt", "policy.pdf", "guide.docx", "page.html"]
_STOP = set("a an the and or of to in on at for is are was be do does i my me we our you your it its this that what "
            "which who when where how why can should must with from by as if any".split())
_DIM = 1024


def _embed_text(text: str) -> tuple[float, ...]:
    v = [0.0] * _DIM
    for tok in re.findall(r"[a-z0-9]+", text.lower()):
        if tok in _STOP:
            continue
        v[int(hashlib.md5(tok.encode()).hexdigest(), 16) % _DIM] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return tuple(x / n for x in v)


class MemoryKnowledge:
    def __init__(self):
        self.rows = []
        for name in _DOCS:
            data = (DOCS / name).read_bytes()
            for d in chunk(extract(sniff(name, data), data), ChunkConfig()):
                self.rows.append((Passage(chunk_id=f"{name}#{d.ordinal}", kb_id="kb-all", document_id=name,
                                          document_name=name, text=d.text, token_count=d.token_count, score=0.0,
                                          meta=d.meta), _embed_text(d.text)))

    async def search(self, ref, qvec, *, top_k, threshold):
        scored = sorted(((sum(a * b for a, b in zip(qvec, vec)), p) for p, vec in self.rows),
                        key=lambda t: -t[0])
        hits = [Passage(**{**p.__dict__, "score": s}) for s, p in scored if s >= threshold][:top_k]
        return SearchOutcome("ok", tuple(hits))


@pytest.fixture
def fake_embed(monkeypatch):
    async def _embed(texts, *, provider, model, api_key, timeout=None, region=None, **_resilience):
        return EmbeddingResult(vectors=tuple(_embed_text(t) for t in texts), usage=NormalizedUsage(1, 0),
                               cost_usd=Decimal(0), cost_is_estimate=True)
    monkeypatch.setattr(gateway, "embed", _embed)


def _load(name):
    return [json.loads(l) for l in (QSETS / name).read_text().splitlines() if l.strip()]


async def _hits(questions, knowledge) -> int:
    ref = RetrievalRef(kb_id="kb-all", embedding_model="fake-bow", injection="always_on", kb_index_version=1,
                       retrieval_config={"top_k": 6, "similarity_threshold": 0.0, "max_ctx_tokens": 2000},
                       embedding_provider="openai", kb_name="All")
    ctx = make_context(knowledge=knowledge, embedding_api_key="k")
    n = 0
    for q in questions:
        res = await retrieve(q["question"], (ref,), ctx)
        if any(p.document_name == q["expected_document"] and q["expected_substring"] in p.text
               for p in res.passages_used):
            n += 1
    return n


def test_question_sets_are_disjoint_and_large_enough():
    dev, held = _load("known_questions.dev.v1.jsonl"), _load("known_questions.heldout.v1.jsonl")
    assert len(dev) >= 20 and len(held) >= 20
    assert not {q["question"] for q in dev} & {q["question"] for q in held}


async def test_sc001_heldout_retrieval_hit_rate(fake_embed, capsys):
    knowledge = MemoryKnowledge()
    dev, held = _load("known_questions.dev.v1.jsonl"), _load("known_questions.heldout.v1.jsonl")
    dev_hits, held_hits = await _hits(dev, knowledge), await _hits(held, knowledge)
    with capsys.disabled():
        print(f"\nSC-001 tuning(dev): {dev_hits}/{len(dev)}   MEASURED(held-out): {held_hits}/{len(held)}")
    assert held_hits >= math.ceil(0.9 * len(held)), f"held-out hit rate {held_hits}/{len(held)} below 90%"

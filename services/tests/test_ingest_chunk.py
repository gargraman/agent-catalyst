"""Structure-aware chunking (feature 024, tasks.md T020; research D8).

`ingestion.chunk.chunk(sections, cfg) -> list[ChunkDraft]`: never crosses a section boundary (structure_aware), every
chunk ≤ `size` tokens, consecutive chunks inside a section overlap by ≈`overlap` tokens, each chunk is an EXACT slice of
its section text (`section.text[char_start:char_end]`) carrying heading/page for citations, ordinals 0..n-1 in document
order, deterministic. Tokens via `runtime.tokens.count_text_tokens` — the same over-estimating heuristic as the
runtime's context budget, so retrieval trimming math agrees with the guard.
"""
from __future__ import annotations

import math

import pytest

from ingestion.chunk import ChunkConfig, ChunkDraft, chunk
from ingestion.extract import Section
from runtime.tokens import count_text_tokens

_SENT = "The quarterly review covers incident trends, staffing levels and the onboarding backlog in detail. "


def _sec(text: str, heading: str | None = None, page: int | None = None) -> Section:
    return Section(text=text, heading=heading, page=page)


def _long_section(n_sentences: int, heading: str = "H", page: int | None = None) -> Section:
    return _sec(_SENT * n_sentences, heading=heading, page=page)


# ------------------------------------------------------------------------------------------------- tokens


def test_count_text_tokens_overestimates_and_is_deterministic():
    assert count_text_tokens("") == 0
    assert count_text_tokens("abc") == 1
    assert count_text_tokens("abcd") == 2                     # ceil(len / 3): rounds UP like count_tokens
    t = "x" * 3000
    assert count_text_tokens(t) == math.ceil(3000 / 3) == count_text_tokens(t)


# ------------------------------------------------------------------------------------------------- config


def test_config_defaults_and_from_empty_dict():
    cfg = ChunkConfig.from_dict({})
    assert (cfg.size, cfg.overlap, cfg.structure_aware) == (800, 120, True)
    assert ChunkConfig() == cfg


@pytest.mark.parametrize("bad", [{"size": 99}, {"size": 2001}, {"size": 800, "overlap": 400},
                                 {"size": 800, "overlap": -1}, {"size": "big"}])
def test_config_validation(bad):
    with pytest.raises(ValueError):
        ChunkConfig.from_dict(bad)


def test_config_accepts_bounds():
    assert ChunkConfig.from_dict({"size": 100, "overlap": 0}).size == 100
    assert ChunkConfig.from_dict({"size": 2000, "overlap": 999}).overlap == 999


# ------------------------------------------------------------------------------------------------- chunking


def test_every_chunk_within_size_and_exact_slice_of_its_section():
    secs = [_long_section(120, "A", page=1), _long_section(60, "B", page=2)]
    cfg = ChunkConfig(size=200, overlap=30)
    out = chunk(secs, cfg)
    assert out and all(isinstance(c, ChunkDraft) for c in out)
    by_heading = {s.heading: s for s in secs}
    for c in out:
        assert c.token_count == count_text_tokens(c.text) <= cfg.size
        src = by_heading[c.meta["heading"]]
        assert src.text[c.meta["char_start"]:c.meta["char_end"]] == c.text
        assert c.meta["page"] == src.page
        assert c.text.strip() == c.text and c.text            # no leading/trailing whitespace, never empty


def test_never_crosses_section_boundary_when_structure_aware():
    a, b = _long_section(30, "Alpha"), _sec("Short beta section about expenses.", "Beta")
    out = chunk([a, b], ChunkConfig(size=200, overlap=30))
    assert {c.meta["heading"] for c in out} == {"Alpha", "Beta"}
    beta = [c for c in out if c.meta["heading"] == "Beta"]
    assert len(beta) == 1 and beta[0].text == "Short beta section about expenses."


def test_consecutive_chunks_overlap_by_about_overlap_tokens():
    sec = _long_section(150, "Long")
    cfg = ChunkConfig(size=200, overlap=40)
    out = [c for c in chunk([sec], cfg)]
    assert len(out) >= 3
    for prev, nxt in zip(out, out[1:]):
        assert nxt.meta["char_start"] < prev.meta["char_end"], "consecutive chunks must overlap"
        shared = sec.text[nxt.meta["char_start"]:prev.meta["char_end"]]
        tok = count_text_tokens(shared)
        assert 0.9 * cfg.overlap <= tok <= 1.1 * cfg.overlap, f"overlap {tok} tokens, want ≈{cfg.overlap}"


def test_zero_overlap_means_adjacent_non_overlapping_chunks():
    sec = _long_section(80, "Z")
    out = chunk([sec], ChunkConfig(size=150, overlap=0))
    for prev, nxt in zip(out, out[1:]):
        assert nxt.meta["char_start"] >= prev.meta["char_end"]


def test_ordinals_are_sequential_in_document_order():
    out = chunk([_long_section(50, "One", 1), _long_section(50, "Two", 2)], ChunkConfig(size=150, overlap=20))
    assert [c.ordinal for c in out] == list(range(len(out)))
    pages = [c.meta["page"] for c in out]
    assert pages == sorted(pages)


def test_single_giant_paragraph_without_sentence_breaks_is_hard_split():
    words = " ".join(f"word{i}" for i in range(3500))          # ~5,000+ tokens, no '.' anywhere
    sec = _sec(words, "Giant")
    cfg = ChunkConfig(size=300, overlap=30)
    out = chunk([sec], cfg)
    assert len(out) > 10
    assert all(c.token_count <= cfg.size for c in out)
    assert out[-1].text.endswith("word3499")                  # nothing dropped at the tail


def test_paragraph_and_sentence_boundaries_preferred_over_mid_sentence():
    para = (_SENT * 6).strip()
    sec = _sec("\n\n".join([para, para, para]), "P")
    out = chunk([sec], ChunkConfig(size=200, overlap=0))
    for c in out[:-1]:
        assert c.text.endswith("."), f"chunk should end on a sentence boundary, ends with: {c.text[-20:]!r}"


def test_whitespace_only_sections_are_skipped():
    out = chunk([_sec("   \n\t ", "Empty"), _sec("Real content here.", "Real")], ChunkConfig())
    assert [c.meta["heading"] for c in out] == ["Real"]


def test_small_section_is_a_single_chunk():
    out = chunk([_sec("Annual leave is 27 days.", "Leave")], ChunkConfig())
    assert len(out) == 1 and out[0].text == "Annual leave is 27 days." and out[0].ordinal == 0


def test_chunking_is_deterministic():
    secs = [_long_section(90, "D", 3)]
    cfg = ChunkConfig(size=180, overlap=25)
    assert chunk(secs, cfg) == chunk(secs, cfg)

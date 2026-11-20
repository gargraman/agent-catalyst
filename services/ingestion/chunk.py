"""Structure-aware chunker (feature 024, research D8).

Splits each extracted Section independently (never across a heading/page boundary when `structure_aware`), preferring
paragraph → sentence → whitespace breaks, hard-splitting only when a window has none. Works in CHARACTER OFFSETS so every
chunk is an exact slice `section.text[char_start:char_end]` (stable citations, deterministic). Sizes are in tokens via
`runtime.tokens.count_text_tokens` (ceil(chars/3)), so `size` tokens ⇔ at most `3·size` characters. Consecutive chunks of
a section overlap by ≈`overlap` tokens, snapped forward to a word start. Pure.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from runtime.tokens import _CHARS_PER_TOKEN, count_text_tokens

_PARA = re.compile(r"\n[ \t]*\n")
_SENT = re.compile(r"[.!?](?=\s)")
_MIN_FILL = 0.3   # a preferred break must leave the chunk at least this full, else fall back to the next level


@dataclass(frozen=True)
class ChunkConfig:
    size: int = 800
    overlap: int = 120
    structure_aware: bool = True

    @classmethod
    def from_dict(cls, d: dict | None) -> "ChunkConfig":
        d = d or {}
        size = d.get("size", 800)
        overlap = d.get("overlap", 120)
        structure_aware = d.get("structure_aware", True)
        if not isinstance(size, int) or isinstance(size, bool) or not 100 <= size <= 2000:
            raise ValueError("chunk_config.size must be an integer in 100..2000")
        if not isinstance(overlap, int) or isinstance(overlap, bool) or not (0 <= overlap and overlap * 2 < size):
            raise ValueError("chunk_config.overlap must be an integer with 0 <= overlap < size/2")
        if not isinstance(structure_aware, bool):
            raise ValueError("chunk_config.structure_aware must be a boolean")
        return cls(size=size, overlap=overlap, structure_aware=structure_aware)

    def canonical(self) -> dict:
        """Normalized form used for dedup keys (FR-006): `{}` and explicit defaults are identical."""
        return {"overlap": self.overlap, "size": self.size, "structure_aware": self.structure_aware}


@dataclass(frozen=True)
class ChunkDraft:
    text: str
    token_count: int
    ordinal: int
    meta: dict = field(default_factory=dict)


def _skip_ws(text: str, i: int) -> int:
    while i < len(text) and text[i].isspace():
        i += 1
    return i


def _rstrip_end(text: str, start: int, end: int) -> int:
    while end > start and text[end - 1].isspace():
        end -= 1
    return end


def _best_break(text: str, start: int, limit: int) -> int:
    """Exclusive end index of the best break in (start + min_fill, limit]."""
    lo = start + int((limit - start) * _MIN_FILL)
    window = text[start:limit + 1]
    paras = [start + m.start() for m in _PARA.finditer(window) if lo < start + m.start() <= limit]
    if paras:
        return paras[-1]
    sents = [start + m.end() for m in _SENT.finditer(window) if lo < start + m.end() <= limit]
    if sents:
        return sents[-1]
    for i in range(limit, lo, -1):
        if i < len(text) and text[i].isspace():
            return i
    return limit                       # no whitespace at all: hard split


def _overlap_start(text: str, prev_start: int, prev_end: int, overlap_chars: int) -> int:
    if overlap_chars <= 0:
        return _skip_ws(text, prev_end)
    target = prev_end - overlap_chars
    if target <= prev_start:
        return _skip_ws(text, prev_end)
    i = target
    while i < prev_end and not (not text[i].isspace() and (i == 0 or text[i - 1].isspace())):
        i += 1
    return i if prev_start < i < prev_end else _skip_ws(text, prev_end)


def _split(text: str, cfg: ChunkConfig) -> list[tuple[int, int]]:
    max_chars = cfg.size * _CHARS_PER_TOKEN
    overlap_chars = cfg.overlap * _CHARS_PER_TOKEN
    spans: list[tuple[int, int]] = []
    start = _skip_ws(text, 0)
    while start < len(text):
        tail_end = _rstrip_end(text, start, len(text))
        if tail_end - start <= max_chars:
            spans.append((start, tail_end))
            break
        brk = _best_break(text, start, start + max_chars)
        end = _rstrip_end(text, start, brk)
        if end <= start:                                   # degenerate (only whitespace before limit)
            end = start + max_chars
        spans.append((start, end))
        nxt = _overlap_start(text, start, end, overlap_chars)
        if nxt <= start:
            nxt = _skip_ws(text, end)
        start = nxt
    return spans


def chunk(sections, cfg: ChunkConfig) -> list[ChunkDraft]:
    if not cfg.structure_aware:
        from ingestion.extract import Section
        sections = [Section("\n\n".join(s.text for s in sections if s.text.strip()))]
    out: list[ChunkDraft] = []
    for sec in sections:
        if not sec.text or not sec.text.strip():
            continue
        for start, end in _split(sec.text, cfg):
            piece = sec.text[start:end]
            out.append(ChunkDraft(
                text=piece,
                token_count=count_text_tokens(piece),
                ordinal=len(out),
                meta={"heading": sec.heading, "page": sec.page, "char_start": start, "char_end": end},
            ))
    return out

"""The injected knowledge-access port (contracts/runtime.md §1). The API implements it over pgvector with tenancy, the
KB's CURRENT access policy, archive state and the frozen embedding-model filter. The runtime only sees these values."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Protocol

if TYPE_CHECKING:
    from runtime.snapshot import RetrievalRef


@dataclass(frozen=True)
class Passage:
    """One retrieved chunk. In-process only — passage text never reaches the run record or logs."""

    chunk_id: str
    kb_id: str
    document_id: str
    document_name: str
    text: str
    token_count: int
    score: float
    meta: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SearchOutcome:
    """`unavailable` covers both "denied by policy" and "archived" so the two are indistinguishable (no leak)."""

    status: Literal["ok", "unavailable", "stale"]
    passages: tuple[Passage, ...] = ()


class KnowledgeAccess(Protocol):
    async def search(self, ref: "RetrievalRef", qvec: list[float], *, top_k: int,
                     threshold: float) -> SearchOutcome:
        """At most `top_k` passages with score ≥ threshold, score-descending. Never raises for "not entitled"
        (that is status="unavailable"); infrastructure errors may raise and the runtime degrades that binding."""
        ...

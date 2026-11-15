"""Retrieval (feature 024 / M7). Pure: the runtime orchestrates retrieval; the API supplies the `KnowledgeAccess` port
(research D1). Nothing here imports web or DB code (P6)."""
from runtime.retrieval.port import KnowledgeAccess, Passage, SearchOutcome

__all__ = ["KnowledgeAccess", "Passage", "SearchOutcome"]

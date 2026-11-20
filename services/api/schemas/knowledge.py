"""Pydantic contracts for knowledge-base metadata (feature 006, US3) — mirror data.ts KnowledgeBase.

Feature 024 (M7) adds real ingestion: KB reads carry derived document/chunk counts and ingestion status computed from
`kb_documents` / `ingestion_jobs`; documents + jobs have their own contracts below.
"""
from __future__ import annotations

import datetime

from pydantic import BaseModel, Field

from api.schemas.access_policy import AccessPolicy


class KnowledgeBaseCreate(BaseModel):
    slug: str
    name: str
    embedding_model: str
    embedding_dim: int = 1024
    chunk_config: dict = {}
    access_policy: dict | None = None       # feature 013


class KnowledgeBasePatch(BaseModel):
    name: str | None = None
    embedding_model: str | None = None
    chunk_config: dict | None = None
    access_policy: dict | None = None       # feature 013


class KnowledgeBaseOut(BaseModel):
    id: str
    slug: str
    name: str
    embedding_model: str
    embedding_dim: int
    chunk_config: dict
    index_version: int
    ingestion_status: str           # derived (feature 024): empty | ingesting | stale | partial | failed | ready
    archived: bool
    created_at: datetime.datetime
    access_policy: AccessPolicy
    document_count: int = 0
    chunk_count: int = 0            # live chunks (active generations)
    last_ingested_at: datetime.datetime | None = None
    embedding_provider: str | None = None   # derived from the model's profile (disclosure, FR-032)


class JobProgress(BaseModel):
    done: int
    total: int


class IngestionJobOut(BaseModel):
    """Feature 024: real ingestion jobs. The first six fields keep the pre-024 shape (data.ts IngestionJob)."""
    id: str
    source: str                     # "upload" (P2 adds connector kinds)
    kb: str                         # kb id
    status: str                     # queued | processing | ready | failed
    docs: int
    chunks: int
    kb_id: str
    document_id: str
    kind: str                       # upload | retry | reingest
    progress: JobProgress
    skipped_unchanged: bool
    cost_usd: str                   # 6-dp string (exact)
    cost_is_estimate: bool
    error: dict | None = None       # {code, message, retryable} — client-safe, never exception text
    created_at: datetime.datetime
    started_at: datetime.datetime | None = None
    finished_at: datetime.datetime | None = None


class DocumentOut(BaseModel):
    """A KB document. Internal fields (storage key, content hash, dedup key) are never exposed."""
    id: str
    kb_id: str
    name: str
    media_type: str
    size_bytes: int
    status: str                     # queued | processing | ready | failed | stale
    chunk_count: int
    live: bool                      # a generation is being served to retrieval
    error: dict | None = None
    injection_flags: dict[str, int] = Field(default_factory=dict)   # {category: count} — counts only, never payload
    created_at: datetime.datetime
    updated_at: datetime.datetime


class UploadAccepted(BaseModel):
    document: DocumentOut
    job: IngestionJobOut


class ReingestAccepted(BaseModel):
    enqueued: int
    skipped_active: int

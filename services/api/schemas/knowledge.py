"""Pydantic contracts for knowledge-base metadata (feature 006, US3) — mirror data.ts KnowledgeBase.

Metadata lifecycle only. Real ingestion (documents/embeddings/jobs) is deferred to M7 (FR-009): the
ingestion-jobs surface reports an honest empty/deferred state, never fabricated doc/chunk counts.
"""
from __future__ import annotations

import datetime

from pydantic import BaseModel

from api.schemas.access_policy import AccessPolicy


class KnowledgeBaseCreate(BaseModel):
    slug: str
    name: str
    embedding_model: str
    embedding_dim: int = 1536
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
    ingestion_status: str           # honest deferred marker (FR-009)
    archived: bool
    created_at: datetime.datetime
    access_policy: AccessPolicy


class IngestionJobOut(BaseModel):
    id: str
    source: str
    kb: str
    status: str
    docs: int
    chunks: int

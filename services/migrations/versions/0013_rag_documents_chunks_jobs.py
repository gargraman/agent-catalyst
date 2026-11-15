"""0013 RAG storage — pgvector + kb_documents / kb_chunks / ingestion_jobs + the stale-job reaper (feature 024, M7)

* `CREATE EXTENSION vector` (the compose + CI Postgres images ship pgvector).
* `knowledge_bases.last_ingested_at` (nullable).
* Three workspace-scoped tables, RLS ENABLED + FORCED with the standard `{table}_ws_isolation` policy (0005 pattern).
  Atomic per-file replace uses generations (research D4): chunks carry `generation`; retrieval reads only
  `generation = kb_documents.active_generation AND active_generation > 0`.
* At most ONE active (queued|processing) job per document, enforced here (partial unique index, analyze H3).
* `ingestion_reap_stale(interval)`: the only cross-tenant path. Ingestion runs in-process (Q3 = A); a process that dies
  leaves jobs stuck, and the jobs it must reap live in every workspace — invisible to ac_app under RLS. Like
  `api_token_resolve` (0008) it is a narrow SECURITY DEFINER function: it only fails jobs whose heartbeat (or, for queued
  jobs, creation) is older than `stale_after`, and resets their documents. It returns a count and nothing else, so it
  discloses no tenant data. ac_app gets EXECUTE on it and no other bypass. The heartbeat lease is what makes it safe
  with multiple replicas: a live replica's jobs keep fresh heartbeats and are never reaped (research D5).

Revision ID: 0013
Revises: 0012
"""
from __future__ import annotations

from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

_TABLES = ("kb_documents", "kb_chunks", "ingestion_jobs")

_MEDIA_TYPES = (
    "'text/plain','text/markdown','text/html','application/pdf',"
    "'application/vnd.openxmlformats-officedocument.wordprocessingml.document'"
)


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY {table}_ws_isolation ON {table}
          USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
          WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
        """
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO ac_app")


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("ALTER TABLE knowledge_bases ADD COLUMN last_ingested_at timestamptz NULL")

    op.execute(
        f"""
        CREATE TABLE kb_documents (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id      uuid NOT NULL REFERENCES workspaces(id),
            kb_id             uuid NOT NULL REFERENCES knowledge_bases(id),
            name              text NOT NULL,
            media_type        text NOT NULL CHECK (media_type IN ({_MEDIA_TYPES})),
            size_bytes        bigint NOT NULL CHECK (size_bytes > 0),
            content_hash      text NOT NULL,                 -- sha256 of raw bytes; never returned to clients
            storage_key       text NOT NULL,                 -- ws/{{ws}}/kb/{{kb}}/doc/{{id}}/{{sha256}}; never returned
            source_kind       text NOT NULL DEFAULT 'upload',
            status            text NOT NULL CHECK (status IN ('queued','processing','ready','failed','stale')),
            active_generation int NOT NULL DEFAULT 0,        -- 0 = nothing live yet
            chunk_count       int NOT NULL DEFAULT 0,
            dedup_key         text NULL,                     -- sha256(content_hash + embedding_model + chunk_config), FR-006
            injection_flags   jsonb NOT NULL DEFAULT '{{}}',   -- {{category: count}} from the ingest-time scan
            error             jsonb NULL,                    -- {{code, message, retryable}}, non-secret
            created_by        text NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            updated_at        timestamptz NOT NULL DEFAULT now(),
            UNIQUE (workspace_id, kb_id, name)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE kb_chunks (
            id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id         uuid NOT NULL REFERENCES workspaces(id),
            kb_id                uuid NOT NULL,             -- denormalized for namespace pruning
            document_id          uuid NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
            generation           int NOT NULL,
            ordinal              int NOT NULL,
            text                 text NOT NULL,             -- customer-confidential
            token_count          int NOT NULL,
            embedding            vector(1024) NOT NULL,     -- single deployment dimension (research D3, revised 2026-11-14)
            embedding_model      text NOT NULL,
            meta                 jsonb NOT NULL DEFAULT '{}',
            injection_categories text[] NOT NULL DEFAULT '{}',
            created_at           timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        "CREATE INDEX kb_chunks_embedding_hnsw ON kb_chunks "
        "USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)"
    )
    op.execute("CREATE INDEX kb_chunks_doc_gen ON kb_chunks (workspace_id, kb_id, document_id, generation)")

    op.execute(
        """
        CREATE TABLE ingestion_jobs (
            id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            workspace_id      uuid NOT NULL REFERENCES workspaces(id),
            kb_id             uuid NOT NULL REFERENCES knowledge_bases(id),
            document_id       uuid NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
            kind              text NOT NULL CHECK (kind IN ('upload','retry','reingest')),
            status            text NOT NULL CHECK (status IN ('queued','processing','ready','failed')),
            progress_done     int NOT NULL DEFAULT 0,
            progress_total    int NOT NULL DEFAULT 0,
            skipped_unchanged boolean NOT NULL DEFAULT false,
            cost_usd          numeric(12,6) NOT NULL DEFAULT 0,
            cost_is_estimate  boolean NOT NULL DEFAULT false,
            error             jsonb NULL,
            owner             text NULL,                    -- executing process (lease)
            heartbeat_at      timestamptz NULL,
            created_by        text NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now(),
            started_at        timestamptz NULL,
            finished_at       timestamptz NULL
        )
        """
    )
    op.execute("CREATE INDEX ingestion_jobs_kb_recent ON ingestion_jobs (workspace_id, kb_id, created_at DESC)")
    op.execute(
        "CREATE INDEX ingestion_jobs_active_heartbeat ON ingestion_jobs (heartbeat_at) "
        "WHERE status IN ('queued','processing')"
    )
    op.execute(
        "CREATE UNIQUE INDEX ingestion_jobs_one_active_per_doc ON ingestion_jobs (document_id) "
        "WHERE status IN ('queued','processing')"
    )

    for t in _TABLES:
        _enable_rls(t)

    op.execute(
        """
        CREATE FUNCTION ingestion_reap_stale(stale_after interval)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            n integer;
        BEGIN
            WITH reaped AS (
                UPDATE ingestion_jobs
                   SET status = 'failed',
                       -- jsonb_build_object, not a JSON literal: alembic's text() treats colon-prefixed words as bind params
                       error = jsonb_build_object('code', 'interrupted',
                                                  'message', 'ingestion was interrupted; retry',
                                                  'retryable', true),
                       finished_at = now()
                 WHERE status IN ('queued','processing')
                   AND coalesce(heartbeat_at, created_at) < now() - stale_after
                RETURNING document_id
            ), docs AS (
                UPDATE kb_documents d
                   SET status = CASE WHEN d.active_generation = 0 THEN 'failed' ELSE 'ready' END,
                       updated_at = now()
                 WHERE d.id IN (SELECT document_id FROM reaped)
                RETURNING d.id
            )
            SELECT count(*) INTO n FROM reaped;
            RETURN n;
        END
        $$
        """
    )
    op.execute("REVOKE ALL ON FUNCTION ingestion_reap_stale(interval) FROM PUBLIC")
    op.execute("GRANT EXECUTE ON FUNCTION ingestion_reap_stale(interval) TO ac_app")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS ingestion_reap_stale(interval)")
    for t in reversed(_TABLES):
        op.execute(f"DROP POLICY IF EXISTS {t}_ws_isolation ON {t}")
    op.execute("DROP TABLE IF EXISTS ingestion_jobs")
    op.execute("DROP TABLE IF EXISTS kb_chunks")
    op.execute("DROP TABLE IF EXISTS kb_documents")
    op.execute("ALTER TABLE knowledge_bases DROP COLUMN IF EXISTS last_ingested_at")
    # The vector extension is intentionally kept: other objects may depend on it and dropping it is not reversible-safe.

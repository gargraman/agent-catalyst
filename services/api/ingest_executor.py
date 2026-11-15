"""In-process ingestion executor + stale-job reaper (feature 024, tasks.md T029; research D5; spec FR-008, Q3 = A).

* A process-wide singleton (`get_executor()`), created lazily — httpx's ASGITransport runs no lifespan, and the upload
  route must be able to submit without it. Concurrency is bounded by `AC_INGEST_CONCURRENCY`.
* Each job runs `api.ingest_pipeline.process_job` against `DbIngestRepo` (own short sessions), the configured storage
  backend, and an embedder bound to the KB model's PROFILE: OpenAI (dev-only) resolves the workspace key just-in-time
  (feature 014 custody); Bedrock (customer data) uses the process's AWS credential chain + `AC_AWS_REGION`.
* While a job runs, a ticker refreshes `heartbeat_at` every `AC_INGEST_HEARTBEAT_S`; the reaper
  (`ingestion_reap_stale`, SECURITY DEFINER, migration 0013) fails only jobs whose heartbeat is older than the lease, so
  a live replica's jobs are never reaped. One sweep runs at startup (lifespan) and then periodically.
* Task exceptions are logged, never raised into the loop; a crashed job still ends `failed` (pipeline contract).
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

from sqlalchemy import select, text

import datetime
import time

from api import db, metrics
from api.db import tenant_session
from api.errors import ConfigError
from api.ingest_pipeline import process_job
from api.ingest_repo import DbIngestRepo
from api.models import IngestionJob, KbDocument, KnowledgeBase, Workspace
from api.settings import get_settings
from api.storage import get_storage
from runtime.providers import gateway
from runtime.providers.ir import EmbeddingError
from api.resilience_wiring import get_guard
from runtime.providers.profiles import resolve_embedding_profile_by_model

log = logging.getLogger("ac.ingest")
_OWNER = f"{socket.gethostname()}:{os.getpid()}"


async def reap_once() -> int:
    """One stale-job sweep on a plain ac_app session (no tenant GUC — the function is the cross-tenant path)."""
    secs = get_settings().ingest_stale_after_s
    async with db.app_engine().begin() as conn:
        n = int((await conn.execute(text("select ingestion_reap_stale(make_interval(secs => :s))"),
                                    {"s": secs})).scalar_one())
    if n:
        log.warning("reaped %d stale ingestion job(s)", n)
        metrics.record_ingestion_reaped(n)
    return n


async def _job_model(ws: str, job_id: str) -> str:
    async with tenant_session(ws) as s:
        return (await s.execute(select(KnowledgeBase.embedding_model)
                                .join(IngestionJob, IngestionJob.kb_id == KnowledgeBase.id)
                                .where(IngestionJob.id == job_id))).scalar_one()


async def _embedder_for(ws: str, model: str):
    """Embedder bound to the model's profile. A key problem surfaces as a non-retryable EmbeddingError so the job ends
    `failed` honestly instead of crashing."""
    try:
        profile = resolve_embedding_profile_by_model(model)
    except FileNotFoundError as exc:          # legacy/unprofiled model: fail INSIDE the pipeline (non-retryable branch)
        missing = exc

        async def unsupported(texts: list[str]):
            raise missing
        return unsupported, 100
    settings = get_settings()
    api_key: str | None = None
    key_error: EmbeddingError | None = None
    if profile.provider != "bedrock":
        from api.provider_keys import resolve_provider_key
        try:
            async with tenant_session(ws) as s:
                api_key = await resolve_provider_key(s, ws, profile.provider)
        except ConfigError:
            key_error = EmbeddingError("embedding provider key unavailable", type="config_error", retryable=False)

    async def embed(texts: list[str]):
        if key_error is not None:
            raise key_error
        return await gateway.embed(texts, provider=profile.provider, model=profile.model, api_key=api_key,
                                   region=settings.aws_region if profile.provider == "bedrock" else None,
                                   guard=get_guard(), traffic_class="batch")   # 025: ingestion is BATCH traffic

    return embed, max(1, profile.max_batch or 100)


class IngestionExecutor:
    def __init__(self) -> None:
        self._tasks: set[asyncio.Task] = set()
        self._sem: asyncio.Semaphore | None = None
        self._sem_loop: asyncio.AbstractEventLoop | None = None
        self._reaper: asyncio.Task | None = None

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._sem is None or self._sem_loop is not loop:           # one semaphore per event loop
            self._sem, self._sem_loop = asyncio.Semaphore(get_settings().ingest_concurrency), loop
        return self._sem

    def submit(self, job_id: str, workspace_id: str) -> None:
        task = asyncio.get_running_loop().create_task(self._run(str(job_id), str(workspace_id)))
        self._tasks.add(task)                                         # retained: no GC of a running job
        task.add_done_callback(self._tasks.discard)

    async def _run(self, job_id: str, ws: str) -> None:
        # Heartbeat from submission, not from slot acquisition: a job waiting its turn belongs to a LIVE process and
        # must not look stale to the reaper (code review 2026-11-15 #2).
        repo = DbIngestRepo(ws, owner=_OWNER)
        ticker = asyncio.get_running_loop().create_task(self._tick(repo, job_id))
        try:
            async with self._semaphore():
                model = await _job_model(ws, job_id)
                embedder, batch = await _embedder_for(ws, model)
                t0 = time.perf_counter()
                await process_job(job_id, repo=repo, storage=get_storage(), embedder=embedder,
                                  heartbeat=lambda: repo.heartbeat(job_id), batch_size=batch)
                metrics.record_ingestion_job(await repo.status(job_id), time.perf_counter() - t0)
        except Exception:  # noqa: BLE001 — never raise into the loop; the reaper recovers a job left processing
            log.exception("ingestion task failed before the pipeline could finalize", extra={"job_id": job_id})
        finally:
            ticker.cancel()                     # also on cancellation while still waiting for a slot

    async def _tick(self, repo: DbIngestRepo, job_id: str) -> None:
        interval = get_settings().ingest_heartbeat_s
        try:
            while True:
                await asyncio.sleep(interval)
                try:
                    await repo.heartbeat(job_id)
                except Exception:  # noqa: BLE001 — a missed beat is tolerated (lease = several beats)
                    log.warning("heartbeat failed", extra={"job_id": job_id})
        except asyncio.CancelledError:
            pass

    async def drain(self, timeout: float | None = None) -> None:
        """Await every submitted job (test hook + graceful shutdown)."""
        loop = asyncio.get_running_loop()
        while True:
            pending = [t for t in self._tasks if not t.done() and t.get_loop() is loop]
            if not pending:
                return
            await asyncio.wait(pending, timeout=timeout)
            if timeout is not None:
                return

    def start_reaper(self, interval_s: float = 60.0) -> None:
        if self._reaper is not None and not self._reaper.done():
            return
        self._reaper = asyncio.get_running_loop().create_task(self._reap_loop(interval_s))

    async def _reap_loop(self, interval_s: float) -> None:
        last_sweep = 0.0
        while True:
            try:
                await reap_once()
            except Exception:  # noqa: BLE001 — a DB blip must not kill the loop
                log.warning("ingestion reaper sweep failed")
            if time.monotonic() - last_sweep >= 3600:                 # orphan sweep hourly (H5)
                last_sweep = time.monotonic()
                try:
                    await sweep_orphans()
                except Exception:  # noqa: BLE001
                    log.warning("orphan sweep failed")
            await asyncio.sleep(interval_s)

    async def stop_reaper(self) -> None:
        if self._reaper is not None:
            self._reaper.cancel()
            try:
                await self._reaper
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._reaper = None


async def sweep_orphans(*, grace_s: int | None = None, now: datetime.datetime | None = None) -> int:
    """Delete stored objects that no `kb_documents.storage_key` references and that are older than the grace period
    (analyze H5 — crash between put and commit, or between delete-commit and object delete). Per workspace: the
    `workspaces` table is the tenant root (not RLS-scoped); live keys are read on THAT workspace's tenant session and
    only its own `ws/{id}/` prefix is listed — no cross-tenant SQL. Best-effort per workspace; returns deleted count."""
    grace = grace_s if grace_s is not None else get_settings().storage_orphan_grace_s
    cutoff = (now or datetime.datetime.now(datetime.timezone.utc)) - datetime.timedelta(seconds=grace)
    storage = get_storage()
    async with db.app_engine().connect() as conn:
        ws_ids = [str(r[0]) for r in (await conn.execute(select(Workspace.id))).all()]
    deleted = 0
    for ws in ws_ids:
        try:
            objects = await storage.list(f"ws/{ws}/")
            if not objects:
                continue
            async with tenant_session(ws) as s:
                live = {k for (k,) in (await s.execute(select(KbDocument.storage_key))).all()}
            for key, modified in objects:
                if key not in live and modified < cutoff:
                    await storage.delete(key)
                    deleted += 1
        except Exception:  # noqa: BLE001 — one workspace's failure must not stop the sweep
            log.warning("orphan sweep failed for one workspace")
    if deleted:
        metrics.record_storage_orphans_deleted(deleted)
    return deleted


_EXECUTOR: IngestionExecutor | None = None


def get_executor() -> IngestionExecutor:
    global _EXECUTOR
    if _EXECUTOR is None:
        _EXECUTOR = IngestionExecutor()
    return _EXECUTOR

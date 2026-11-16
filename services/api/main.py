"""FastAPI application assembly (M4, feature 005).

The thin HTTP layer wrapping the pure runtime (M2a) + evals (M3). Wires the typed error envelope
(`api/errors.app_error_handler`) and the three routers (agents, invoke, evals). OpenAPI 3.1 is emitted from
the Pydantic schemas and is the source of truth for the generated frontend client (M12).
"""
from __future__ import annotations

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api import db, metrics, redis_client
from api.csrf import CSRFMiddleware
from api.settings import get_settings
from api.errors import AppError, app_error_handler
from api.routers import (
    agents, auth, documents, evals, invoke, knowledge, members, provider_keys, runs, skills, tokens, tools, workspaces,
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Feature 024 (research D5): one stale-ingestion-job sweep at startup, then the periodic reaper; on shutdown stop
    the reaper and give in-flight ingestion a bounded drain (anything cut off is reaped as `interrupted` later)."""
    from api.ingest_executor import get_executor, reap_once
    try:
        await reap_once()
    except Exception:  # noqa: BLE001 — a DB that is not up yet must not block startup; the loop retries
        logging.getLogger("ac.ingest").warning("startup ingestion sweep failed")
    ex = get_executor()
    ex.start_reaper(60.0)
    try:
        yield
    finally:
        await ex.stop_reaper()
        await ex.drain(timeout=10.0)


def _bind_provider_transport() -> None:
    """Feature 026 (D22): adapters/gateway call the module attributes `transport.stream_chunks` / `transport.embed`
    (the existing test seam), so the dev-only fake is bound by assigning them once, api-side — runtime/ stays pure.
    `api.settings` already refuses `fake` under AC_ENV=prod; this is a no-op for the default `litellm`."""
    if get_settings().provider_transport != "fake":
        return
    from runtime.providers import fake_transport, transport
    transport.stream_chunks = fake_transport.stream_chunks
    transport.embed = fake_transport.embed
    logging.getLogger("ac.providers").warning("AC_PROVIDER_TRANSPORT=fake — provider calls are SCRIPTED (dev only)")


def create_app() -> FastAPI:
    _bind_provider_transport()
    app = FastAPI(title="Agent Catalyst API", version="0.5.0", lifespan=_lifespan)
    # Typed exception → envelope ({"error": {code, message, detail?}}). Starlette matches subclasses via MRO,
    # so one registration covers every AppError leaf (InputInvalid, AccessDenied, ConflictStale, …).
    app.add_exception_handler(AppError, app_error_handler)

    # Feature 019: double-submit CSRF over cookie-authenticated state changes (one choke point, D5 exemptions).
    # Added last → runs OUTERMOST, so a bad state-changing request is refused before any routing/auth work.
    app.add_middleware(CSRFMiddleware)

    app.include_router(auth.router)               # feature 019: /auth/login, /auth/callback, /auth/logout
    app.include_router(agents.router)
    app.include_router(invoke.router)
    app.include_router(invoke.published_router)   # feature 018: published caller-token invoke plane (/v1)
    app.include_router(tokens.router)             # feature 018: caller-token mint/list/revoke + admin revoke-user
    app.include_router(evals.router)
    app.include_router(tools.router)
    app.include_router(skills.router)
    app.include_router(knowledge.router)
    app.include_router(documents.router)          # feature 024: documents + ingestion jobs
    app.include_router(workspaces.router)
    app.include_router(workspaces.discovery_router)   # GET /api/v1/workspaces — pre-selection discovery (switcher)
    app.include_router(members.router)
    app.include_router(provider_keys.router)
    app.include_router(runs.router)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        """Liveness — the process is up. No dependency checks (never flaps on a slow DB)."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["meta"])
    async def readyz() -> Response:
        """Readiness (orchestration probe, deployment infra slice) — the service can serve traffic:
        verifies DB connectivity via the app (RLS) engine, and — when Redis is configured — that the shared
        cache is reachable. Returns 503 not-ready if either dependency is down so a load balancer / k8s
        readiness gate holds/drains traffic until the stack is up (migrations run first).

        Redis readiness (feature 021, D3): a *configured-but-unreachable* Redis flips /readyz to 503 because
        the 018 deny-set fails CLOSED on it — every authenticated console request would 401, so the instance
        genuinely cannot serve traffic and should be drained, not left answering a wall of 401s. Parity carve-
        out (mirrors `denyset.is_denied`): when AC_REDIS_URL is UNSET (dev/test, no deny-set to consult),
        Redis is NOT a readiness dependency and /readyz stays 200 — only a configured Redis that errors trips."""
        try:
            async with db.app_engine().connect() as conn:
                await conn.execute(text("select 1"))
        except Exception:                                    # noqa: BLE001 — any DB failure → not ready
            return JSONResponse({"status": "not-ready", "checks": {"database": "down"}}, status_code=503)
        checks = {"database": "ok"}
        # Redis is a readiness dependency only when configured (carve-out: unset → not consulted, stays 200).
        if get_settings().redis_url:
            if not await redis_client.is_healthy():
                checks["redis"] = "down"
                return JSONResponse({"status": "not-ready", "checks": checks}, status_code=503)
            checks["redis"] = "ok"
        return JSONResponse({"status": "ready", "checks": checks})

    # Feature 010 (F12): unauthenticated, non-/api/v1 Prometheus scrape surface (aggregate counts only —
    # no tenant/content). Network restriction is a deploy concern (FR-011).
    @app.get("/metrics", tags=["meta"])
    async def metrics_endpoint() -> Response:
        body, content_type = metrics.render()
        return Response(content=body, media_type=content_type)

    return app


app = create_app()

"""FastAPI application assembly (M4, feature 005).

The thin HTTP layer wrapping the pure runtime (M2a) + evals (M3). Wires the typed error envelope
(`api/errors.app_error_handler`) and the three routers (agents, invoke, evals). OpenAPI 3.1 is emitted from
the Pydantic schemas and is the source of truth for the generated frontend client (M12).
"""
from __future__ import annotations

from fastapi import FastAPI, Response
from fastapi.responses import JSONResponse
from sqlalchemy import text

from api import db, metrics
from api.errors import AppError, app_error_handler
from api.routers import (
    agents, evals, invoke, knowledge, members, runs, skills, tools, workspaces,
)


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Catalyst API", version="0.5.0")
    # Typed exception → envelope ({"error": {code, message, detail?}}). Starlette matches subclasses via MRO,
    # so one registration covers every AppError leaf (InputInvalid, AccessDenied, ConflictStale, …).
    app.add_exception_handler(AppError, app_error_handler)

    app.include_router(agents.router)
    app.include_router(invoke.router)
    app.include_router(evals.router)
    app.include_router(tools.router)
    app.include_router(skills.router)
    app.include_router(knowledge.router)
    app.include_router(workspaces.router)
    app.include_router(members.router)
    app.include_router(runs.router)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        """Liveness — the process is up. No dependency checks (never flaps on a slow DB)."""
        return {"status": "ok"}

    @app.get("/readyz", tags=["meta"])
    async def readyz() -> Response:
        """Readiness (orchestration probe, deployment infra slice) — the service can serve traffic:
        verifies DB connectivity via the app (RLS) engine. Returns 503 not-ready if the DB is unreachable,
        so a load balancer / k8s readiness gate holds traffic until the stack is up (migrations run first)."""
        try:
            async with db.app_engine().connect() as conn:
                await conn.execute(text("select 1"))
        except Exception:                                    # noqa: BLE001 — any DB failure → not ready
            return JSONResponse({"status": "not-ready", "checks": {"database": "down"}}, status_code=503)
        return JSONResponse({"status": "ready", "checks": {"database": "ok"}})

    # Feature 010 (F12): unauthenticated, non-/api/v1 Prometheus scrape surface (aggregate counts only —
    # no tenant/content). Network restriction is a deploy concern (FR-011).
    @app.get("/metrics", tags=["meta"])
    async def metrics_endpoint() -> Response:
        body, content_type = metrics.render()
        return Response(content=body, media_type=content_type)

    return app


app = create_app()

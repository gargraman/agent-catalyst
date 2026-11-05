"""FastAPI application assembly (M4, feature 005).

The thin HTTP layer wrapping the pure runtime (M2a) + evals (M3). Wires the typed error envelope
(`api/errors.app_error_handler`) and the three routers (agents, invoke, evals). OpenAPI 3.1 is emitted from
the Pydantic schemas and is the source of truth for the generated frontend client (M12).
"""
from __future__ import annotations

from fastapi import FastAPI

from api.errors import AppError, app_error_handler
from api.routers import agents, evals, invoke


def create_app() -> FastAPI:
    app = FastAPI(title="Agent Catalyst API", version="0.5.0")
    # Typed exception → envelope ({"error": {code, message, detail?}}). Starlette matches subclasses via MRO,
    # so one registration covers every AppError leaf (InputInvalid, AccessDenied, ConflictStale, …).
    app.add_exception_handler(AppError, app_error_handler)

    app.include_router(agents.router)
    app.include_router(invoke.router)
    app.include_router(evals.router)

    @app.get("/healthz", tags=["meta"])
    async def healthz() -> dict:
        return {"status": "ok"}

    return app


app = create_app()

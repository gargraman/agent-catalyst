"""Pydantic request/response/SSE contracts for the thin API (M4, feature 005).

These models are the OpenAPI 3.1 source of truth and mirror `frontend/src/lib/data.ts`. Pure data — no DB,
no web handler logic — so they stay cheap to import and test.
"""

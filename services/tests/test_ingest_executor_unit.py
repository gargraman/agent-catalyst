"""Executor wiring that needs no DB (code review 2026-11-15 #3).

A KB whose embedding model has no profile (legacy / removed) must fail through the pipeline's own NON-retryable
"embedding model is not supported" branch — not raise before the pipeline, leave the job queued, and get reaped as
"interrupted, retryable" forever."""
from __future__ import annotations

import pytest

from api.ingest_executor import _embedder_for


async def test_unprofiled_model_yields_an_embedder_that_fails_like_unsupported():
    embed, batch = await _embedder_for("00000000-0000-0000-0000-000000000001", "legacy-embedder-v0")
    assert batch >= 1
    with pytest.raises(FileNotFoundError):
        await embed(["anything"])

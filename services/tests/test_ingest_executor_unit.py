"""Executor wiring that needs no DB (code review 2026-11-15 #3).

A KB whose embedding model has no profile (legacy / removed) must fail through the pipeline's own NON-retryable
"embedding model is not supported" branch — not raise before the pipeline, leave the job queued, and get reaped as
"interrupted, retryable" forever."""
from __future__ import annotations

import pytest

from api.ingest_executor import _embedder_for
from runtime.providers import gateway


async def test_unprofiled_model_yields_an_embedder_that_fails_like_unsupported():
    embed, batch = await _embedder_for("00000000-0000-0000-0000-000000000001", "legacy-embedder-v0")
    assert batch >= 1
    with pytest.raises(FileNotFoundError):
        await embed(["anything"])


# feature 025 T019 (US2, FR-010/012): ingestion embeddings run as BATCH traffic through the API's process Guard
async def test_unavailable_ingest_embedder_passes_batch_class_and_process_guard(monkeypatch):
    from api import ingest_executor, resilience_wiring
    seen: dict = {}

    async def fake_embed(texts, **kwargs):
        seen.update(kwargs)
        raise RuntimeError("stop here")
    monkeypatch.setattr(gateway, "embed", fake_embed)
    resilience_wiring.reset_for_tests()
    try:
        # bedrock profile: keyless, so no DB/secrets lookup happens in _embedder_for
        embed, _ = await _embedder_for("00000000-0000-0000-0000-000000000001", "amazon.titan-embed-text-v2:0")
        with pytest.raises(RuntimeError):
            await embed(["anything"])
        assert seen["traffic_class"] == "batch"
        assert seen["guard"] is resilience_wiring.get_guard()
    finally:
        resilience_wiring.reset_for_tests()

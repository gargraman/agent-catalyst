"""FR-025 consistency check — the degraded-mode decision record stays tied to the code + tests it cites.

Not a behavior test (the matrix in ``test_redis_degraded_mode.py`` proves behavior); this guards the DOC against
silent drift (P9 docs clause): every control named in the record must have its cited test function and cited
source file actually present, so the record can't quietly describe code that moved or disappeared.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_DOC = _REPO_ROOT / "docs" / "backend" / "degraded-mode.md"


@pytest.fixture(scope="module")
def doc_text() -> str:
    assert _DOC.exists(), f"degraded-mode decision record missing at {_DOC} (FR-025 deliverable)"
    return _DOC.read_text()


@pytest.mark.parametrize("control", ["deny-set", "rate limiter", "idempotency", "spend cap", "/readyz"])
def test_record_documents_each_control(doc_text, control):
    assert control.lower() in doc_text.lower(), f"decision record omits control: {control}"


@pytest.mark.parametrize("policy", ["fail closed", "fail open", "recovery"])
def test_record_states_policies(doc_text, policy):
    assert policy.lower() in doc_text.lower(), f"decision record omits policy term: {policy}"


def test_cited_matrix_tests_exist(doc_text):
    """Each degraded-matrix test the record names must actually exist (doc ↔ test linkage)."""
    mod = importlib.import_module("tests.test_redis_degraded_mode")
    for name in (
        "test_denyset_fails_closed_both_planes",
        "test_ratelimit_fails_open_and_alerts",
        "test_spend_cap_fails_closed",
        "test_idempotency_fails_closed_retryable",
        "test_controls_resume_after_redis_recovers",
        "test_readyz_503_when_configured_redis_down",
    ):
        assert name in doc_text, f"decision record no longer cites {name}"
        assert hasattr(mod, name), f"cited test {name} does not exist in the matrix module"


def test_cited_isolation_test_exists(doc_text):
    mod = importlib.import_module("tests.test_pgbouncer_txn_isolation")
    assert "test_pgbouncer_txn_isolation.py" in doc_text
    assert hasattr(mod, "test_no_guc_leak_across_pooled_requests")


def test_cited_source_files_exist(doc_text):
    for rel in (
        "services/api/auth/denyset.py",
        "services/api/ratelimit.py",
        "services/api/deps.py",
        "services/api/metrics.py",
        "services/api/tenancy.py",
        "services/api/main.py",
        "services/api/redis_client.py",
        "deploy/compose.yaml",
    ):
        assert rel in doc_text, f"decision record no longer cites {rel}"
        assert (_REPO_ROOT / rel).exists(), f"cited source file is missing: {rel}"

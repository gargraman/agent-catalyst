"""M0.0.a (RED) — the AppError hierarchy + 08 error envelope (00L §1).

The (http_status, code) pairs are contract declarations (already green). The envelope SHAPE
is the behavior under test and is driven RED by the NotImplementedError stub.
"""
from __future__ import annotations

import pytest

from api import errors as E

# (class, expected_status, expected_code) — one row per 00L §1.2 entry.
CASES = [
    (E.InputInvalid, 400, "input.invalid"),
    (E.AuthRequired, 401, "auth.required"),
    (E.BudgetExceeded, 402, "budget.exceeded"),
    (E.AccessDenied, 403, "access.denied"),
    (E.NotFound, 404, "not_found"),
    (E.ConflictError, 409, "conflict"),
    (E.ConflictStale, 409, "conflict.stale"),
    (E.EvalGateError, 409, "publish.eval_gate"),
    (E.LowConfidenceError, 409, "publish.low_confidence"),
    (E.RegistrationGate, 409, "registration.gate"),
    (E.ToolApprovalDenied, 409, "tool.approval.denied"),
    (E.IdempotencyInProgress, 409, "idempotency.in_progress"),
    (E.AssertionDetailRequired, 422, "eval.assertion_detail_required"),
    (E.RateLimited, 429, "rate.limited"),
    (E.ProviderUnavailable, 502, "provider.unavailable"),
]


@pytest.mark.parametrize("cls,status,code", CASES)
def test_status_and_code(cls, status, code):
    err = cls("boom")
    assert err.http_status == status
    assert err.code == code


@pytest.mark.parametrize("cls,status,code", CASES)
def test_envelope_shape(cls, status, code):
    err = cls("boom")
    assert err.envelope() == {"error": {"code": code, "message": "boom"}}


def test_envelope_includes_detail_when_present():
    err = E.EvalGateError("no pass", detail={"last_pass_at": None})
    assert err.envelope() == {
        "error": {
            "code": "publish.eval_gate",
            "message": "no pass",
            "detail": {"last_pass_at": None},
        }
    }


def test_envelope_omits_detail_when_absent():
    assert "detail" not in E.NotFound("nope").envelope()["error"]


def test_409_family_are_conflict_subclasses():
    for cls in (E.EvalGateError, E.LowConfidenceError, E.RegistrationGate,
                E.ToolApprovalDenied, E.IdempotencyInProgress, E.ConflictStale):
        assert issubclass(cls, E.ConflictError)
        assert cls.http_status == 409


def test_apperror_default_is_500_internal():
    assert E.AppError.http_status == 500
    assert E.AppError.code == "internal"

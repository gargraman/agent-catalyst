"""HTTP-surfacing error hierarchy + envelope (00L §1).

Contract-first: the class hierarchy and the (http_status, code) pairs ARE the contract. The
envelope() logic is implemented; the FastAPI handler imports FastAPI lazily so the infra-free
unit tests don't require it.
"""
from __future__ import annotations


class AppError(Exception):
    """Base for every HTTP-surfacing error (category A, 00L §1).

    Subclasses set ``http_status`` + ``code``; ``detail`` is the optional machine-readable
    context object from the 08 envelope.
    """

    http_status: int = 500
    code: str = "internal"

    def __init__(self, message: str, *, detail: dict | None = None, headers: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail
        # Optional response headers carried on the error (e.g. 429 Retry-After / X-RateLimit-* — feature 016).
        # Back-compatible: defaults to None, so every existing raise site is unaffected.
        self.headers = headers

    def envelope(self) -> dict:
        """Return the 08 error envelope: ``{"error": {"code", "message", "detail"?}}``."""
        body: dict = {"code": self.code, "message": self.message}
        if self.detail is not None:
            body["detail"] = self.detail
        return {"error": body}


# --- 4xx leaf classes (one per 08 §1 / 00L §1.2 row) ---
class InputInvalid(AppError):
    http_status, code = 400, "input.invalid"


class AuthRequired(AppError):
    http_status, code = 401, "auth.required"


class AccessDenied(AppError):
    http_status, code = 403, "access.denied"


class NotFound(AppError):
    http_status, code = 404, "not_found"


class BudgetExceeded(AppError):
    http_status, code = 402, "budget.exceeded"


class AssertionDetailRequired(AppError):
    http_status, code = 422, "eval.assertion_detail_required"


class RateLimited(AppError):
    http_status, code = 429, "rate.limited"


# --- 409 family: same status, distinct codes ---
class ConflictError(AppError):
    http_status, code = 409, "conflict"


class ConflictStale(ConflictError):
    code = "conflict.stale"


class EvalGateError(ConflictError):
    code = "publish.eval_gate"


class LowConfidenceError(ConflictError):
    code = "publish.low_confidence"


class RegistrationGate(ConflictError):
    code = "registration.gate"


class ToolApprovalDenied(ConflictError):
    code = "tool.approval.denied"


class IdempotencyInProgress(ConflictError):
    code = "idempotency.in_progress"


# --- 5xx ---
class ProviderUnavailable(AppError):
    http_status, code = 502, "provider.unavailable"


class ConfigError(AppError):
    """Fail-closed configuration refusal (feature 014, FR-014): the workspace has no active provider key
    for the agent's provider, or the secrets backend is unavailable. Raised BEFORE any provider call so a
    run never starts without a credential. 503 (not 500): an operator fix — capture/rotate a key — clears
    it; it is not an internal bug. Message carries no secret and no vault reference (FR-017)."""
    http_status, code = 503, "config_error"


# Refusal codes counted for observability (feature 010, F12) — requests refused by a deterministic control
# that never become a run. NOTE: budget.exceeded is run-admission-specific (paid paths), while access.denied
# spans ALL gated routes (incl. reads) — the `code` label distinguishes them; the counter is named
# ac_requests_refused_total (requests, not runs) to be honest about that scope.
_REFUSAL_CODES = frozenset({"budget.exceeded", "access.denied"})


def app_error_handler(request, exc: AppError):  # pragma: no cover — wired in api.main (M4)
    """FastAPI exception handler → JSONResponse(exc.http_status, exc.envelope()).

    FastAPI is imported lazily so M0's infra-free unit tests don't require it. Also counts admission refusals
    (budget.exceeded / access.denied) for the /metrics surface — best-effort, never affects the response.
    """
    from fastapi.responses import JSONResponse

    if exc.code in _REFUSAL_CODES:
        from api import metrics
        metrics.record_refusal(exc.code)   # F12 refusal counter (best-effort — record_refusal swallows)

    return JSONResponse(status_code=exc.http_status, content=exc.envelope(), headers=exc.headers)

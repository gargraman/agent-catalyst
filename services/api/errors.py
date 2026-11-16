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


class EvalKnowledgeForbidden(AccessDenied):
    """Feature 024 / analyze H1: the eval caller cannot USE a KB bound to the evaluated version (no run, no cost)."""
    code = "eval.knowledge_forbidden"


class CsrfInvalid(AppError):
    """Double-submit CSRF check failed (feature 019): a cookie-authenticated state-changing request arrived
    without a `csrf` cookie matching its `X-CSRF-Token` header. 403 — a distinct code from `access.denied` so
    the UI can prompt a refresh rather than treat it as an authz denial. Raised/returned by the CSRF middleware
    (outside the AppError exception handler), so the middleware builds the envelope from this class directly."""
    http_status, code = 403, "csrf.invalid"


class NotFound(AppError):
    http_status, code = 404, "not_found"


class BudgetExceeded(AppError):
    http_status, code = 402, "budget.exceeded"


class AssertionDetailRequired(AppError):
    http_status, code = 422, "eval.assertion_detail_required"


class RateLimited(AppError):
    http_status, code = 429, "rate.limited"


class EgressBlocked(AppError):
    """SSRF / egress guard refusal (feature 017, F19): an outbound HTTP tool call was refused because its
    target resolved to a private/loopback/link-local/multicast/metadata address, was not on the per-tool
    domain allow-list, redirected to an internal host, or re-resolved to a blocked IP (DNS rebind).

    Fail-closed (FR-027). The client-visible envelope carries ONLY a coarse `reason` code (FR-009) — never
    the resolved IP, the full target URL, or any secret. 502: this is an outbound/upstream-leg refusal (the
    platform declined to dial the requested destination), distinct from caller authz (403) and spend (402)."""
    http_status, code = 502, "egress.blocked"


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


# --- feature 024 (M7 ingestion) ---
class KbArchived(ConflictError):
    code = "kb.archived"


class AgentArchived(ConflictError):
    """An archived agent is read-only (feature 026 D4): only the restore transition is accepted."""
    code = "agent.archived"


class VersionFrozen(ConflictError):
    """A committed version's bindings are immutable (feature 025 US4, FR-018 — `frozen_binding:` trigger refusal)."""
    code = "version.frozen"


class DocumentProcessing(ConflictError):
    """At most one active ingestion job per document (analyze H3; enforced by a partial unique index)."""
    code = "document.processing"


class JobNotRetryable(ConflictError):
    code = "job.not_retryable"


class UploadTooLarge(AppError):
    http_status, code = 413, "upload.too_large"


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

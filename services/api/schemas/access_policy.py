"""Typed per-resource access-policy contract (feature 013, FR-018).

ONE shape shared by the stored JSONB, the API wire body, and `frontend/src/lib/access-policy.ts` — **camelCase**
field names verbatim (`visibility`, `useRoles`, `useTeams`, `manageRoles`, `manageTeams`, `owner`) so the FE
preview, the OpenAPI client, and the DB column never diverge. The pure evaluator (`api/access_policy.py`) decides
access from this shape; this module is only the (de)serialization + input-validation contract.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from api.access_policy import parse_policy
from api.errors import InputInvalid

Visibility = Literal["workspace", "restricted", "private"]


class AccessPolicy(BaseModel):
    """Wire/stored access-policy. Unknown keys are rejected (fail-closed input); unknown `visibility` is rejected
    at write time by the Literal — the evaluator still fails closed on any value it doesn't recognize on read."""
    model_config = ConfigDict(extra="forbid")

    visibility: Visibility = "workspace"
    useRoles: list[str] = []
    useTeams: list[str] = []
    manageRoles: list[str] = []
    manageTeams: list[str] = []
    owner: str | None = None


def validate_access_policy(raw: dict | None) -> dict | None:
    """Validate+normalize an inbound access_policy body into the canonical stored dict, or raise `InputInvalid`
    (400 `input.invalid`) on a malformed one. Returns `None` when `raw` is None (field omitted → leave the row's
    existing/default policy untouched)."""
    if raw is None:
        return None
    try:
        model = AccessPolicy.model_validate(raw)
    except ValidationError as exc:
        raise InputInvalid("malformed access_policy", detail={"field": "access_policy",
                                                              "errors": exc.error_count()}) from exc
    return model.model_dump(exclude_none=False)


def policy_for_store(raw: dict | None) -> dict:
    """Normalized access_policy dict to persist on create. A missing/None body → the ungoverned `workspace`
    default (so a created row behaves exactly as pre-013). Malformed → `InputInvalid`."""
    return validate_access_policy(raw) or AccessPolicy().model_dump(exclude_none=False)


def stamp_private_owner(stored: dict | None, subject: str | None) -> dict | None:
    """FR-012: when a policy is `private` with no `owner`, stamp the acting subject as owner — so a resource is
    never created or edited into an ownerless `private` state reachable only by admins. No-op for any other tier,
    an already-owned policy, or a `None` stored (field omitted on patch)."""
    if not isinstance(stored, dict) or stored.get("visibility") != "private" or stored.get("owner"):
        return stored
    return {**stored, "owner": subject} if subject else stored


def policy_out(stored: dict | None) -> AccessPolicy:
    """Project a stored access_policy (possibly a minimal `{"visibility":"workspace"}` legacy row, or None) into
    the fully-populated typed shape for an API response — via the evaluator's fail-closed `parse_policy`."""
    return AccessPolicy.model_validate(parse_policy(stored))

"""Feature 013 — structured access-policy deny log (FR-014).

A denied USE/MANAGE must emit ONE structured, non-secret log line (capability + tier + subject + resource) so an
operator can see who was denied what, without leaking a token/secret. Uses the pure helpers directly (no DB).
"""
from __future__ import annotations

import logging

from api.access_policy import PolicyPrincipal
from api.deps import ensure_manage, ensure_use
from api.errors import AccessDenied, NotFound

RESTRICTED = {"visibility": "restricted", "useRoles": ["builder"], "useTeams": ["SOC"],
              "manageRoles": ["publisher"], "manageTeams": ["SOC"]}
OUTSIDER = PolicyPrincipal(role="builder", teams=("IR",), subject="outsider")
USER_NOT_MANAGER = PolicyPrincipal(role="builder", teams=("SOC",), subject="insider")


def test_denied_use_emits_structured_non_secret_log(caplog):
    with caplog.at_level(logging.INFO, logger="api.access_policy"):
        try:
            ensure_use(RESTRICTED, OUTSIDER, not_found=NotFound("agent not found", detail={"agent_id": "a-123"}))
        except NotFound:
            pass
    recs = [r for r in caplog.records if r.name == "api.access_policy"]
    assert recs, "a denied USE must emit an access_policy deny log line"
    msg = recs[-1].getMessage()
    assert "use" in msg and "restricted" in msg and "outsider" in msg and "a-123" in msg, msg
    # FR-014: no secret/token in the deny log
    assert "sk-" not in msg and "token" not in msg.lower() and "secret" not in msg.lower(), msg


def test_denied_manage_emits_structured_log(caplog):
    with caplog.at_level(logging.INFO, logger="api.access_policy"):
        try:
            ensure_manage(RESTRICTED, USER_NOT_MANAGER,
                          not_found=NotFound("agent not found", detail={"agent_id": "a-9"}))
        except AccessDenied:
            pass
    recs = [r for r in caplog.records if r.name == "api.access_policy"]
    assert recs and "manage" in recs[-1].getMessage(), "a denied MANAGE must emit a deny log line"

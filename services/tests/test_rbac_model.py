"""Foundational (feature 008, T002): RBAC permission model + parity with frontend permissions.ts.

Pure (no DB, no app). RED until api.permissions + the generated rbac_permissions.json exist (T003–T005)."""
from __future__ import annotations

import pathlib

from api.permissions import ROLE_PERMISSIONS, has_permission
from scripts.gen_rbac import PERMISSIONS_TS, parse_permissions_ts

EXPECTED_VIEWER = {"view", "playground.use"}
BUILDER_ADDS = {"agent.create", "agent.edit", "eval.run", "eval.author",
                "knowledge.manage", "tool.manage", "mcp.manage", "skill.manage", "channel.manage"}
PUBLISHER_ADDS = {"agent.publish", "tool.approve"}
ADMIN_ADDS = {"agent.delete", "key.manage", "member.manage", "access.manage", "settings.manage", "eval.govern"}


def test_role_permission_sets_match_the_model():
    assert set(ROLE_PERMISSIONS["viewer"]) == EXPECTED_VIEWER
    assert set(ROLE_PERMISSIONS["builder"]) == EXPECTED_VIEWER | BUILDER_ADDS
    assert set(ROLE_PERMISSIONS["publisher"]) == EXPECTED_VIEWER | BUILDER_ADDS | PUBLISHER_ADDS
    assert set(ROLE_PERMISSIONS["admin"]) == EXPECTED_VIEWER | BUILDER_ADDS | PUBLISHER_ADDS | ADMIN_ADDS


def test_has_permission_is_fail_closed_for_unknown_role():
    assert has_permission("admin", "member.manage") is True
    assert has_permission("builder", "agent.publish") is False
    for perm in ("view", "agent.create", "member.manage"):
        assert has_permission("superadmin", perm) is False      # unknown role → nothing
        assert has_permission("", perm) is False
        assert has_permission(None, perm) is False               # type: ignore[arg-type]


def test_server_map_has_no_drift_from_permissions_ts():
    """FR-010/SC-004: the committed server map equals a fresh parse of permissions.ts."""
    fresh = parse_permissions_ts(pathlib.Path(PERMISSIONS_TS).read_text())
    assert {r: set(p) for r, p in ROLE_PERMISSIONS.items()} == {r: set(p) for r, p in fresh.items()}

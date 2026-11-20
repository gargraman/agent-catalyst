"""Unit tests for the pure access-policy evaluator (feature 013).

Asserts `can_use`/`can_manage` against the canonical decision table in
`tests/fixtures/access_policy_cases.json`, which mirrors `frontend/src/lib/access-policy.ts` verbatim
(FR-013 / SC-003). No DB, no HTTP — pure evaluation. This is the FE↔BE parity artifact.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from api.access_policy import PolicyPrincipal, can_manage, can_use

_CASES = json.loads((pathlib.Path(__file__).parent / "fixtures" / "access_policy_cases.json").read_text())


def _principal(spec: dict) -> PolicyPrincipal:
    return PolicyPrincipal(role=spec["role"], teams=tuple(spec.get("teams") or ()), subject=spec.get("subject"))


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_decision_table_matches_access_policy_ts(case: dict) -> None:
    p = _principal(case["principal"])
    assert can_use(case["policy"], p) is case["use"], f"USE mismatch for {case['name']}"
    assert can_manage(case["policy"], p) is case["manage"], f"MANAGE mismatch for {case['name']}"


def test_admin_always_allowed_regardless_of_policy() -> None:
    admin = PolicyPrincipal(role="admin", teams=(), subject="a1")
    for policy in (
        {"visibility": "restricted"},
        {"visibility": "restricted", "useTeams": ["X"], "manageTeams": ["Y"]},
        {"visibility": "private", "owner": "someone-else"},
        {"visibility": "weird"},
    ):
        assert can_use(policy, admin) is True
        assert can_manage(policy, admin) is True


def test_empty_restricted_is_fail_closed_for_non_admin() -> None:
    p = PolicyPrincipal(role="publisher", teams=("SOC",), subject="u1")
    assert can_use({"visibility": "restricted"}, p) is False
    assert can_manage({"visibility": "restricted"}, p) is False


def test_executer_never_satisfies_private() -> None:
    ex = PolicyPrincipal(role="executer", teams=("SOC",), subject=None)
    assert can_use({"visibility": "private", "owner": "SOC"}, ex) is False
    assert can_manage({"visibility": "private", "owner": "SOC"}, ex) is False

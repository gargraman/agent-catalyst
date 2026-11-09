"""Pure per-resource access-policy evaluator (feature 013).

The backend's authoritative port of `frontend/src/lib/access-policy.ts` — the SAME three-tier contract
(`workspace` / `restricted` / `private`), the SAME fail-closed `matches` rule, the SAME executer team-only
rule, private-owner rule, and admin bypass. Decisions are proven identical to the FE via the canonical
decision-table fixture (`tests/fixtures/access_policy_cases.json`, FR-013 / SC-003).

PURE: no FastAPI / HTTP / DB imports (constitution P6). API-layer enforcement (routers + deps) calls these
functions; the invoke path wraps `runtime.invoke` at the router so `runtime/` stays framework-free.

Field names match `access-policy.ts` verbatim (camelCase: `useRoles`, `useTeams`, `manageRoles`,
`manageTeams`, `owner`) so the stored JSON and the API schema share one shape (FR-018).
"""
from __future__ import annotations

from dataclasses import dataclass

_WORKSPACE = "workspace"
_RESTRICTED = "restricted"
_PRIVATE = "private"
_ADMIN = "admin"
_EXECUTER = "executer"


@dataclass(frozen=True)
class PolicyPrincipal:
    """The evaluator's view of a caller.

    role: one of viewer|builder|publisher|admin (console) or 'executer' (runtime/token plane).
    teams: the caller's team memberships (from the members store for a console caller; from the token/channel
           plane for an executer). subject: console identity (owner match); None for an executer (no owner id).
    """
    role: str
    teams: tuple[str, ...] = ()
    subject: str | None = None

    @property
    def is_executer(self) -> bool:
        return self.role == _EXECUTER


def parse_policy(raw: dict | None) -> dict:
    """Normalize a stored/input access_policy into a safe, fully-populated shape. Fail-closed:
    a missing/empty/non-dict policy becomes the ungoverned `workspace` default; list fields coerce to lists;
    an unknown `visibility` is preserved verbatim so the evaluator denies non-admins for it."""
    if not isinstance(raw, dict) or not raw:
        return {"visibility": _WORKSPACE, "useRoles": [], "useTeams": [],
                "manageRoles": [], "manageTeams": [], "owner": None}

    def _list(x: object) -> list[str]:
        return [str(i) for i in x] if isinstance(x, list) else []

    owner = raw.get("owner")
    return {
        "visibility": raw.get("visibility", _WORKSPACE),
        "useRoles": _list(raw.get("useRoles")),
        "useTeams": _list(raw.get("useTeams")),
        "manageRoles": _list(raw.get("manageRoles")),
        "manageTeams": _list(raw.get("manageTeams")),
        "owner": str(owner) if owner else None,
    }


def _matches(roles: list[str], teams: list[str], p: PolicyPrincipal) -> bool:
    """The `restricted` rule, mirroring access-policy.ts `matches`.

    - An empty restriction (no role AND no team) grants no one (fail closed).
    - An executer caller has no console role → gated on TEAMS ALONE (a role-only policy denies it).
    - A console caller: role AND team (an empty list = unconstrained on that dimension).
    """
    if not roles and not teams:
        return False
    if p.is_executer:
        return bool(teams) and bool(set(p.teams) & set(teams))
    role_ok = (not roles) or (p.role in roles)
    team_ok = (not teams) or bool(set(p.teams) & set(teams))
    return role_ok and team_ok


def _is_owner(policy: dict, p: PolicyPrincipal) -> bool:
    return bool(p.subject) and bool(policy["owner"]) and p.subject == policy["owner"]


def can_use(policy: dict | None, principal: PolicyPrincipal) -> bool:
    """Can this principal USE (invoke / bind / read) the resource? Mirrors access-policy.ts `canUse`."""
    pol = parse_policy(policy)
    vis = pol["visibility"]
    if vis == _WORKSPACE:
        return True
    if principal.role == _ADMIN:
        return True
    if vis == _PRIVATE:
        return _is_owner(pol, principal)
    if vis == _RESTRICTED:
        return _matches(pol["useRoles"], pol["useTeams"], principal)
    return False  # unknown visibility → deny (admin already allowed above)


def can_manage(policy: dict | None, principal: PolicyPrincipal) -> bool:
    """Can this principal MANAGE (edit / publish / archive) the resource? Mirrors access-policy.ts `canManage`."""
    pol = parse_policy(policy)
    vis = pol["visibility"]
    if vis == _WORKSPACE:
        return True
    if principal.role == _ADMIN:
        return True
    if vis == _PRIVATE:
        return _is_owner(pol, principal)
    if vis == _RESTRICTED:
        return _matches(pol["manageRoles"], pol["manageTeams"], principal)
    return False

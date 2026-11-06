"""US3 (feature 008, FR-014/SC-007): deny-by-default by construction — every workspace-scoped route MUST
declare a required permission. Pure (introspects app.routes; no DB). A new /api/v1 route without a
require_permission dependency fails this test rather than silently reverting to membership-only."""
from __future__ import annotations

from fastapi.routing import APIRoute

from api.main import app


def _declares_permission(dependant) -> bool:
    """Recursively search a route's dependant tree for a require_permission-produced dependency."""
    for dep in dependant.dependencies:
        if getattr(dep.call, "required_permission", None) is not None:
            return True
        if _declares_permission(dep):
            return True
    return False


def test_every_workspace_scoped_route_declares_a_permission():
    missing = [
        (sorted(r.methods), r.path)
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/api/v1")
        and not _declares_permission(r.dependant)
    ]
    assert not missing, f"/api/v1 routes without a required permission (deny-by-default violation): {missing}"


def test_coverage_check_can_detect_an_unannotated_route():
    """Sanity: the check would actually catch a gap (a route whose dependant has no permission marker)."""
    class _FakeDep:
        dependencies: list = []
    assert _declares_permission(_FakeDep()) is False

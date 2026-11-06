"""US4 — member roster view/add/remove/set-role; role stored, not enforced (feature 006, FR-012)."""
from __future__ import annotations

import pytest

from tests._api_helpers import client, dev_headers, seed_workspace

pytestmark = pytest.mark.integration


async def test_roster_add_list_set_role_remove():
    ws = await seed_workspace()
    async with client() as c:
        added = await c.post("/api/v1/members", headers=dev_headers(workspace=ws),
                             json={"subject": "alice", "display_name": "Alice", "email": "a@x.com",
                                   "console_role": "builder"})
        assert added.status_code == 201
        mid = added.json()["id"]
        lst = (await c.get("/api/v1/members", headers=dev_headers(workspace=ws))).json()
        assert any(m["subject"] == "alice" for m in lst["items"])
        # set role (stored; not enforced)
        pr = await c.patch(f"/api/v1/members/{mid}", headers=dev_headers(workspace=ws),
                           json={"console_role": "publisher"})
        assert pr.status_code == 200 and pr.json()["console_role"] == "publisher"
        # remove → hard delete
        rm = await c.delete(f"/api/v1/members/{mid}", headers=dev_headers(workspace=ws))
        after = (await c.get("/api/v1/members", headers=dev_headers(workspace=ws))).json()
    assert rm.status_code == 204 and all(m["id"] != mid for m in after["items"])


async def test_roster_pagination_reaches_all_members():
    """F3 — the roster is fully pageable (no silent truncation): limit=1 pages cover every member."""
    ws = await seed_workspace()
    subjects = set()
    async with client() as c:
        for i in range(3):
            await c.post("/api/v1/members", headers=dev_headers(workspace=ws), json={"subject": f"s{i}"})
            subjects.add(f"s{i}")
        seen, cursor, pages = set(), None, 0
        while pages < 10:
            url = "/api/v1/members?limit=1" + (f"&cursor={cursor}" if cursor else "")
            page = (await c.get(url, headers=dev_headers(workspace=ws))).json()
            seen.update(m["subject"] for m in page["items"])
            cursor = page["next_cursor"]
            pages += 1
            if not cursor:
                break
    assert subjects == seen and len(seen) == 3


async def test_duplicate_subject_is_409():
    ws = await seed_workspace()
    async with client() as c:
        await c.post("/api/v1/members", headers=dev_headers(workspace=ws), json={"subject": "bob"})
        dup = await c.post("/api/v1/members", headers=dev_headers(workspace=ws), json={"subject": "bob"})
    assert dup.status_code == 409 and dup.json()["error"]["code"].startswith("conflict")

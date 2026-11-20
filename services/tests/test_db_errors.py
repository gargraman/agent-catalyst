"""Feature 025 US4 — `api/db_errors.map_integrity_error` (contract api §1, research D12). Unit, infra-free.

A fake `IntegrityError` mirrors the real SQLAlchemy+asyncpg shape probed on the dev stack: `exc.orig.sqlstate` is the
SQLSTATE, `exc.orig.__cause__` is the asyncpg exception carrying `constraint_name` + `message`.
"""
from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from api.errors import InputInvalid, NotFound


class _Cause(Exception):
    def __init__(self, constraint_name: str | None, message: str) -> None:
        super().__init__(message)
        self.constraint_name, self.message = constraint_name, message


class _Orig(Exception):
    def __init__(self, sqlstate: str, constraint: str | None, message: str) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.__cause__ = _Cause(constraint, message)


def _exc(sqlstate: str, constraint: str | None = None, message: str = "boom") -> IntegrityError:
    return IntegrityError("stmt", {}, _Orig(sqlstate, constraint, message))


@pytest.mark.parametrize("constraint", [
    "fk_skill_bindings_skill", "fk_tool_bindings_tool", "fk_tool_bindings_mcp_tool",
    "fk_retrieval_bindings_kb_ws", "fk_kb_chunks_kb",
    "retrieval_bindings_kb_id_fkey",   # legacy single-column KB FK kept alongside; may fire first (RI order)
])
def test_reference_fk_maps_to_not_found(constraint) -> None:
    from api.db_errors import map_integrity_error
    err = map_integrity_error(_exc("23503", constraint, f'violates foreign key constraint "{constraint}"'))
    assert isinstance(err, NotFound) and err.http_status == 404 and err.code == "not_found"


def test_frozen_binding_maps_to_version_frozen_409() -> None:
    from api.db_errors import map_integrity_error
    from api.errors import VersionFrozen
    err = map_integrity_error(_exc("23001", None, "frozen_binding: skill_bindings row of version 1234 is immutable"))
    assert isinstance(err, VersionFrozen) and err.http_status == 409 and err.code == "version.frozen"


@pytest.mark.parametrize("constraint,message", [
    ("ck_tool_bindings_one_target", 'violates check constraint "ck_tool_bindings_one_target"'),
    (None, "chunk_dim_mismatch: chunk has 1024 dims, knowledge base 99 declares 768"),
])
def test_check_violations_map_to_input_invalid(constraint, message) -> None:
    from api.db_errors import map_integrity_error
    err = map_integrity_error(_exc("23514", constraint, message))
    assert isinstance(err, InputInvalid) and err.http_status == 400 and err.code == "input.invalid"


def test_foreign_active_version_maps_to_input_invalid() -> None:
    from api.db_errors import map_integrity_error
    err = map_integrity_error(_exc("23503", "fk_agents_active_version", "violates fk_agents_active_version"))
    assert isinstance(err, InputInvalid) and err.code == "input.invalid"


@pytest.mark.parametrize("sqlstate,constraint,message", [
    ("23505", "uq_something", "duplicate key"),          # unique: not ours — honest 500 upstream
    ("23503", "agents_workspace_id_fkey", "fk"),          # an FK we did not add
    ("23514", "ck_other", "check"),                        # a CHECK we did not add
    ("23001", None, "restrict but not ours"),              # 23001 without our prefix
])
def test_unknown_violations_are_not_guessed(sqlstate, constraint, message) -> None:
    from api.db_errors import map_integrity_error
    assert map_integrity_error(_exc(sqlstate, constraint, message)) is None


def test_mapped_message_never_echoes_db_text() -> None:
    """Messages carry ids/fixed text only — never the raw DB message (it can name other-workspace rows)."""
    from api.db_errors import map_integrity_error
    secret = 'Key (workspace_id, skill_id)=(aaaa, bbbb) is not present in table "skills" named Confidential-Skill'
    err = map_integrity_error(_exc("23503", "fk_skill_bindings_skill", secret))
    assert "Confidential-Skill" not in str(err) and "Confidential-Skill" not in repr(getattr(err, "detail", ""))


def test_non_integrity_shape_without_orig_is_none() -> None:
    from api.db_errors import map_integrity_error
    assert map_integrity_error(IntegrityError("stmt", {}, Exception("no sqlstate"))) is None


# ---- code review 2026-11-18 #7: only the suite-name UNIQUE is a name conflict ----------------------------------

def test_eval_suite_name_unique_maps_to_conflict_409() -> None:
    from api.db_errors import map_integrity_error
    from api.errors import ConflictError
    err = map_integrity_error(_exc("23505", "uq_eval_suites_agent_name", "duplicate key value"))
    assert isinstance(err, ConflictError) and err.http_status == 409 and err.detail == {"field": "name"}


def test_eval_suite_agent_fk_is_not_a_name_conflict() -> None:
    from api.db_errors import map_integrity_error
    assert map_integrity_error(_exc("23503", "fk_eval_suites_agent", "violates foreign key constraint")) is None

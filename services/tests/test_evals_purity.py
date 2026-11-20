"""Guard (FR-020/P6/SC-008): services/evals stays a pure library — no web/db/api imports.

If evals imported FastAPI or SQLAlchemy (directly or via api/), the eval worker and the HTTP endpoint could
no longer share the exact same runtime.invoke, breaking the drift guarantee. Static source scan (unit).
Mirrors test_runtime_purity.py.
"""
from __future__ import annotations

import pathlib
import re

_EVALS = pathlib.Path(__file__).resolve().parents[1] / "evals"
_FORBIDDEN = re.compile(r"^\s*(?:import|from)\s+(fastapi|starlette|sqlalchemy|asyncpg|api)\b", re.MULTILINE)


def test_evals_has_no_web_or_db_imports():
    offenders = {}
    for py in _EVALS.rglob("*.py"):
        hits = _FORBIDDEN.findall(py.read_text())
        if hits:
            offenders[str(py.relative_to(_EVALS))] = sorted(set(hits))
    assert not offenders, f"evals/ must stay pure (no web/db/api imports): {offenders}"

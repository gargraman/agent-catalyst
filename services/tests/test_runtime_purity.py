"""Guard: services/runtime stays a pure library — no web/db/api imports (00 §3 invariant).

If runtime imported FastAPI or SQLAlchemy, the eval worker and the HTTP endpoint could no longer
share the exact same runtime.invoke, breaking the drift guarantee. Static source scan (unit).
"""
from __future__ import annotations

import pathlib
import re

_RUNTIME = pathlib.Path(__file__).resolve().parents[1] / "runtime"
_FORBIDDEN = re.compile(r"^\s*(?:import|from)\s+(fastapi|starlette|sqlalchemy|api)\b", re.MULTILINE)


def test_runtime_has_no_web_or_db_imports():
    offenders = {}
    for py in _RUNTIME.rglob("*.py"):
        hits = _FORBIDDEN.findall(py.read_text())
        if hits:
            offenders[str(py.relative_to(_RUNTIME))] = sorted(set(hits))
    assert not offenders, f"runtime/ must stay pure (no web/db/api imports): {offenders}"

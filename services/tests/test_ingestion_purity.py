"""Guard: services/ingestion stays a pure library (feature 024, tasks.md T009) — mirrors test_runtime_purity.py.

Extraction + chunking are deterministic text transforms; they must not reach a web framework, the DB, the cloud SDK,
Redis or the network, so they are unit-testable and reusable by a future worker (M11) without the API.
"""
from __future__ import annotations

import pathlib
import re

_INGESTION = pathlib.Path(__file__).resolve().parents[1] / "ingestion"
_FORBIDDEN = re.compile(
    r"^\s*(?:import|from)\s+(fastapi|starlette|sqlalchemy|asyncpg|psycopg|boto3|botocore|redis|httpx|api)\b",
    re.MULTILINE,
)


def test_ingestion_package_exists():
    # The guard below is vacuous on an empty tree; require the package so the guard actually guards something.
    assert (_INGESTION / "__init__.py").is_file(), "services/ingestion/ package is missing"


def test_ingestion_has_no_web_db_or_network_imports():
    offenders = {}
    for py in _INGESTION.rglob("*.py"):
        hits = _FORBIDDEN.findall(py.read_text())
        if hits:
            offenders[str(py.relative_to(_INGESTION))] = sorted(set(hits))
    assert not offenders, f"ingestion/ must stay pure (no web/db/cloud/network imports): {offenders}"

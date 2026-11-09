"""Emit the API's OpenAPI 3.1 document to frontend/openapi.json (the FE client's source of truth).

Deterministic (stable key order from FastAPI + fixed indentation) so CI can regenerate and detect drift.
Run with the services venv: `services/.venv/bin/python scripts/emit_openapi.py`.
NOTE: the strict drift gate against the generated FE client is M12 (FE is not wired yet); CI currently runs
this only as an emit smoke (schema generates cleanly).
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services"))

from api.main import app  # noqa: E402

out = ROOT / "frontend" / "openapi.json"
out.write_text(json.dumps(app.openapi(), indent=2) + "\n")
print(f"wrote {out} ({out.stat().st_size} bytes)")

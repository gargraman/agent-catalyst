"""Generate the server RBAC permission map from the frontend source of truth (feature 008, FR-010).

Parses `frontend/src/lib/permissions.ts` (no Node) — the `BUILDER`/`PUBLISHER`/`ADMIN` permission arrays
and the `ROLE_PERMISSIONS` object, resolving `...SPREAD` references — and writes `services/api/rbac_permissions.json`.
`parse_permissions_ts(text)` is pure + importable so the drift test can re-parse and assert parity.

Usage (from services/):  .venv/bin/python scripts/gen_rbac.py
"""
from __future__ import annotations

import json
import pathlib
import re

_HERE = pathlib.Path(__file__).resolve()
_REPO = _HERE.parents[2]
PERMISSIONS_TS = _REPO / "frontend" / "src" / "lib" / "permissions.ts"
OUT_JSON = _HERE.parents[1] / "api" / "rbac_permissions.json"

_ROLES = ("viewer", "builder", "publisher", "admin")


def _strip_comments(text: str) -> str:
    return re.sub(r"//[^\n]*", "", text)


def _tokens(body: str) -> list[tuple[str, str]]:
    """Ordered tokens in an array body: ('lit', name) for "x", ('spread', NAME) for ...NAME."""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r'"([^"]+)"|\.\.\.([A-Za-z_]\w*)', body):
        out.append(("lit", m.group(1)) if m.group(1) is not None else ("spread", m.group(2)))
    return out


def parse_permissions_ts(text: str) -> dict[str, list[str]]:
    """Return {role: [permissions...]} resolved from permissions.ts text. Order-preserving, deduped."""
    text = _strip_comments(text)
    consts: dict[str, str] = {}
    for m in re.finditer(r"const\s+(BUILDER|PUBLISHER|ADMIN)\s*:\s*Permission\[\]\s*=\s*\[(.*?)\]\s*;", text, re.DOTALL):
        consts[m.group(1)] = m.group(2)

    def resolve(body: str, seen: tuple[str, ...] = ()) -> list[str]:
        acc: list[str] = []
        for kind, val in _tokens(body):
            if kind == "lit":
                if val not in acc:
                    acc.append(val)
            else:
                if val in seen:
                    raise ValueError(f"cyclic spread ...{val}")
                if val not in consts:
                    raise ValueError(f"unknown spread ...{val}")
                for p in resolve(consts[val], seen + (val,)):
                    if p not in acc:
                        acc.append(p)
        return acc

    rp = re.search(r"ROLE_PERMISSIONS\s*:\s*Record<[^>]*>\s*=\s*\{(.*?)\}\s*;", text, re.DOTALL)
    if not rp:
        raise ValueError("ROLE_PERMISSIONS object not found in permissions.ts")
    roles: dict[str, list[str]] = {}
    for rm in re.finditer(r"(viewer|builder|publisher|admin)\s*:\s*(\[[^\]]*\]|[A-Za-z_]\w*)", rp.group(1)):
        role, expr = rm.group(1), rm.group(2).strip()
        if expr.startswith("["):
            roles[role] = resolve(expr)
        elif expr in consts:
            roles[role] = resolve(consts[expr])
        else:
            raise ValueError(f"role {role} references unknown identifier {expr}")
    missing = [r for r in _ROLES if r not in roles]
    if missing:
        raise ValueError(f"missing roles in ROLE_PERMISSIONS: {missing}")
    return {r: roles[r] for r in _ROLES}


def generate() -> dict[str, list[str]]:
    data = parse_permissions_ts(PERMISSIONS_TS.read_text())
    OUT_JSON.write_text(json.dumps(data, indent=2) + "\n")
    return data


if __name__ == "__main__":
    d = generate()
    print(f"wrote {OUT_JSON} — " + ", ".join(f"{r}:{len(p)}" for r, p in d.items()))

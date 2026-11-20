"""The API image must contain every first-party top-level package (code review 2026-11-15 #1: `ingestion/` was missing,
so `import api.main` crashed in the container while every source-tree test passed)."""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_every_top_level_package_is_copied_into_the_image():
    copied = set(re.findall(r"^COPY\s+(\S+)\s+\./", (ROOT / "Dockerfile").read_text(), flags=re.M))
    packages = {p.name for p in ROOT.iterdir()
                if p.is_dir() and (p / "__init__.py").exists() and p.name not in {"tests", "scripts"}}
    assert packages, "no packages found — test is mis-rooted"
    assert packages <= copied, f"Dockerfile does not COPY: {sorted(packages - copied)}"


def test_make_up_rebuilds_images():
    """`make up` must rebuild the api AND migrate images: without --build, a stale `migrate` image (built before
    migration 0013 existed) crashed the stack with "Can't locate revision '0013'" (024 live check, 2026-11-15)."""
    up = re.search(r"^up:\n\t(.+)$", (ROOT.parent / "Makefile").read_text(), flags=re.M)
    assert up and "--build" in up.group(1), up and up.group(1)


def test_make_test_integration_runs_every_localstack_test():
    """The opt-in LocalStack target must not hand-list files: a new `*_localstack.py` test (024 T074) was silently
    never run by `make test-integration`."""
    target = re.search(r"^test-integration:\n((?:\t.*\n?)+)", (ROOT.parent / "Makefile").read_text(), flags=re.M)
    assert target
    body = target.group(1)
    for f in sorted((ROOT / "tests").glob("*_localstack.py")):
        assert f"tests/{f.name}" in body or "tests/*_localstack.py" in body, f"make test-integration skips {f.name}"

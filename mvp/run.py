"""CLI entrypoint for the viability MVP.

Usage (from the repo root):   python -m mvp.run h2
Or (from mvp/):               python -m run h2

Only `h2` is live in Phase 2; the other subcommands are stubs that name the phase that will fill them.
The shell is expected to have already exported the provider key (e.g. `set -a; . ./.env; set +a`) — this
process only reads os.environ and never prints the key.
"""

import asyncio
import os
import sys

# Make the flat Phase-1 modules (ir, cost, providers, experiments, …) importable regardless of whether
# this is launched as `python -m mvp.run` (from the repo root) or `python -m run` (from mvp/).
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _cmd_h2(_args: list[str]) -> int:
    from experiments.h2_providers import run_h2

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set. Run `set -a; . ./.env; set +a` in mvp/ first.", file=sys.stderr)
        return 2
    asyncio.run(run_h2(api_key=api_key))
    return 0


def _stub(phase: str):
    def _run(_args: list[str]) -> int:
        print(f"not yet ({phase})")
        return 0

    return _run


_COMMANDS = {
    "h2": _cmd_h2,
    "baseline": _stub("Phase 3"),
    "calibrate": _stub("Phase 4"),
    "h1": _stub("Phase 5"),
    "tune": _stub("Phase 6"),
    "scorecard": _stub("Phase 7"),
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in _COMMANDS:
        print(f"usage: python -m mvp.run {{{'|'.join(_COMMANDS)}}}", file=sys.stderr)
        return 2
    return _COMMANDS[argv[0]](argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())

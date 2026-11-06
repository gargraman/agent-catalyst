"""RunContext + RunSink (M2a, feature 003).

The per-invocation envelope the caller supplies to `runtime.invoke`. Pure (P6). The Run is delivered to
the caller-supplied `record_run` sink exactly once at completion, so the runtime never persists directly
(research D2/D3). See specs/003-runtime-invoke-spine/data-model.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Union

if TYPE_CHECKING:
    from runtime.run import RunRecord

# A sink receives the finished RunRecord. May be sync or async; the runtime awaits it if awaitable.
RunSink = Callable[["RunRecord"], Union[Awaitable[None], None]]


@dataclass
class RunContext:
    """Per-invocation call envelope (data-model.md → RunContext).

    `max_tool_calls` is a carried-but-NOT-enforced field in M2a — there is no tool-execution loop to bound
    (Clarifications 2026-11-04 / FR-010). `deadline_s` is the only actively enforced bound (D5)."""

    workspace_id: str                 # tenant the run belongs to; copied onto the RunRecord (P5)
    source: str                       # playground | api | eval | channel | tool_test | subagent
    api_key: str                      # provider credential (config/env in M2a; vault later)
    record_run: RunSink               # invoked exactly once at completion with the finished RunRecord
    caller: str | None = None
    correlation_id: str | None = None # minted by the runtime when absent (no un-traceable run, SC-007)
    deadline_s: float = 30.0          # wall-clock budget, actively enforced (D5)
    max_tool_calls: int = 8           # carried, NOT enforced in M2a (D6)
    max_corrections: int = 2          # dedicated self-correction budget (M2b, FR-010); independent of max_tool_calls

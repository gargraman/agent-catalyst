"""RunContext + RunSink (M2a, feature 003).

The per-invocation envelope the caller supplies to `runtime.invoke`. Pure (P6). The Run is delivered to
the caller-supplied `record_run` sink exactly once at completion, so the runtime never persists directly
(research D2/D3). See specs/003-runtime-invoke-spine/data-model.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Awaitable, Callable, Union

if TYPE_CHECKING:
    from runtime.retrieval.port import KnowledgeAccess
    from runtime.run import ModelCallRecord, RunRecord

# A sink receives the finished RunRecord. May be sync or async; the runtime awaits it if awaitable.
RunSink = Callable[["RunRecord"], Union[Awaitable[None], None]]
# A call sink receives one ModelCallRecord per model turn (feature 023). Best-effort: the runtime swallows its
# failures so telemetry can never fail or alter a run (FR-009).
CallSink = Callable[["ModelCallRecord"], Union[Awaitable[None], None]]


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
    run_id: str | None = None         # caller may supply the run's id (so a pre-run frame can name it); else the runtime mints one
    deadline_s: float = 30.0          # wall-clock budget, actively enforced (D5)
    max_tool_calls: int = 8           # carried, NOT enforced in M2a (D6)
    max_corrections: int = 2          # dedicated self-correction budget (M2b, FR-010); independent of max_tool_calls
    record_call: CallSink | None = None   # per-model-call sink (023); None → no per-call records
    content_logging: bool = False     # effective content-logging flag, carried for the sink; unused by runtime logic
    provider_correlation_id: str | None = None  # platform-generated id for providers (023 FR-014); never a caller value
    # feature 024 (M7): retrieval is orchestrated by the runtime over this API-supplied port (research D1). None with
    # bound KBs ⇒ every binding degrades as "no_knowledge_access" (never a crash, never a silent "used").
    knowledge: "KnowledgeAccess | None" = None
    embedding_api_key: str | None = None      # resolved by the API like the chat key (014); provider may differ
    embedding_region: str | None = None       # Bedrock (customer-data provider) region; key-less AWS credential chain
    record_detection: Callable[[dict], Union[Awaitable[None], None]] | None = None  # injection detections (US3), no text

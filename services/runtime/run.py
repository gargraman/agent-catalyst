"""Runtime result + observability value objects (M2a, feature 003).

Pure, frozen value types — no DB/ORM/HTTP (P6). `RunRecord` is the complete observability record the
runtime PRODUCES for every invocation (P8); persistence belongs to a later caller (M4). `InvokeResult`
is the collected return for non-streaming callers. See specs/003-runtime-invoke-spine/data-model.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from runtime.providers.ir import NormalizedUsage
from runtime.providers.profiles import Substitution


class Outcome(str, Enum):
    """The honest, closed status taxonomy for one invocation — exactly one applies (P8)."""

    SUCCESS = "success"                   # stream completed with a terminal stop (content may be empty)
    INVALID_INPUT = "invalid_input"       # inputs failed the snapshot input contract (no provider call)
    CONTEXT_OVERFLOW = "context_overflow" # request exceeds the model context budget (no provider call)
    CONFIG_ERROR = "config_error"         # snapshot names an unknown/retired model (fail closed)
    PROVIDER_ERROR = "provider_error"     # gateway surfaced a normalized provider failure
    TIMEOUT = "timeout"                   # deadline reached before terminal stop
    DEGRADED = "degraded"                 # stream ended with no usage report (gateway degraded ERROR)
    OUTPUT_INVALID = "output_invalid"     # json output could not be made schema-valid within budget (M2b, F1/F2)
    TOOL_CALL_REJECTED = "tool_call_rejected"  # a tool call could not be made valid within budget (M2b, F3)
    BOUNDED_EXIT = "bounded_exit"         # RESERVED — tool-call loop bound; not produced (M2a D6 / M2b unused)


@dataclass(frozen=True)
class RunRecord:
    """Complete record of one invocation. Superset of the frontend `Run` display type; the M4 sink maps
    it to the stored/returned shape. Produced for EVERY invocation regardless of outcome (FR-011)."""

    run_id: str
    correlation_id: str
    workspace_id: str
    agent_id: str
    version_id: str
    version_label: str
    provider: str
    model: str
    profile_hash: str
    prompt_hash: str
    snapshot_hash: str
    source: str
    outcome: Outcome
    latency_ms: int
    caller: str | None = None
    max_tool_calls: int | None = None             # carried from ctx (FR-010); not enforced in M2a (D6)
    correction_turns: int = 0                     # extra model turns spent on self-correction (M2b, FR-014); 0 when clean
    usage: NormalizedUsage | None = None          # absent on pre-call rejections
    cost_usd: Decimal | None = None               # carried from the gateway, never recomputed (FR-014/P7)
    cost_is_estimate: bool = False
    substitutions: tuple[Substitution, ...] = ()  # M1 ledger carried through unchanged (FR-017)
    stop_reason: str | None = None
    error: dict | None = None                     # normalized, api_key-scrubbed (P8)
    first_token_ms: int | None = None             # first TEXT_DELTA of the first pass-through turn (023 US3); None in validated mode


@dataclass(frozen=True)
class ModelCallRecord:
    """One model call (= one gateway turn) of a run (feature 023, spec A2). Produced by the runtime once per turn and
    handed to `RunContext.record_call`; NEVER persisted by the runtime. Carries raw text IN-PROCESS ONLY — the API
    sink decides what leaves the process (metadata by default, redacted text on opt-in). Pure (P6)."""

    run_id: str
    correlation_id: str                 # edge-resolved (may be caller-supplied) — never sent to a provider
    provider_correlation_id: str        # platform-generated 32-hex id sent to the provider (FR-014)
    workspace_id: str
    agent_id: str
    version_id: str
    source: str                         # playground | api | eval
    role: str                           # "judge" if version_id == "judge" else "candidate"
    call_seq: int                       # 0-based turn index within the run
    correction: bool                    # True for validated-mode correction turns
    provider: str
    model: str
    prompt_version: str                 # snapshot prompt_hash
    input_text: str                     # raw, in-process only
    output_text: str                    # raw, in-process only
    latency_ms: int
    first_token_ms: int | None
    cost_usd: Decimal                   # the gateway-attached turn cost; 0 when no usage
    cost_known: bool
    success: bool
    error_code: str | None              # normalized error code (no raw provider payload)
    provider_request_id: str | None = None


@dataclass(frozen=True)
class InvokeResult:
    """Collected return for non-streaming callers (eval). Embeds the same RunRecord handed to the sink."""

    text: str
    run: RunRecord
    tool_calls: tuple[dict, ...] = ()
    reasoning: str | None = None
    stop_reason: str | None = None

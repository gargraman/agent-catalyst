"""The provider-gateway intermediate representation (02 §2).

Every caller builds a NormalizedRequest; every adapter streams NormalizedStreamEvents. Nothing
above the gateway imports a provider SDK type. These are pure, frozen data contracts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class NormalizedRequest:
    provider: str
    model: str
    api_version: str | None
    messages: list[dict]                 # role/content, normalized
    system: str | None
    params: dict                         # temperature, max_tokens, top_p … (validated vs profile)
    tools: list[dict] = field(default_factory=list)          # JSON-schema tool defs
    tool_choice: str | dict | None = None
    response_format: dict | None = None  # structured-output JSON schema, if the profile supports it
    reasoning: dict | None = None        # {effort|budget} — only if profile.reasoning_control
    cache_hints: list[dict] = field(default_factory=list)    # prompt-cache breakpoints (07)
    stream: bool = True


class EventType(str, Enum):
    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_DELTA = "tool_call_delta"
    TOOL_CALL_END = "tool_call_end"
    USAGE = "usage"
    STOP = "stop"
    ERROR = "error"


@dataclass(frozen=True)
class NormalizedStreamEvent:
    type: EventType
    data: dict                           # payload by type (02 §2): TEXT_DELTA→{"text"},
                                         # TOOL_CALL_*→{"id","name","args"|"args_delta"},
                                         # USAGE→{"usage": NormalizedUsage}, ERROR→{"error"}


@dataclass(frozen=True)
class NormalizedUsage:
    input_tokens: int                    # provider-reported, INCLUSIVE of cached_input + cache_write
    output_tokens: int                   # provider-reported, INCLUSIVE of reasoning_tokens
    cached_input_tokens: int = 0         # cache READS — discounted rate (07)
    cache_write_tokens: int = 0          # cache SEEDING — cache_write premium (07)
    reasoning_tokens: int = 0            # a SUBSET of output_tokens (not additive)
    cost_usd: float = 0.0                # WE compute this (cost.py), not the provider/LiteLLM

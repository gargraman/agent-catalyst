"""Normalized IR — mirrors docs/backend/02-provider-gateway.md §2 (02:33-72) and §5 (02:216-221).

Everything above the (future) gateway speaks this one normalized dialect; nothing here imports a
provider SDK type. These shapes are deliberately identical to the backend design so Phase-2 findings
port straight into services/runtime/providers/ir.py.
"""

from dataclasses import dataclass, field
from enum import Enum


@dataclass(frozen=True)
class NormalizedRequest:
    # 02:33-46
    provider: str
    model: str
    api_version: str | None
    messages: list[dict]                                    # role/content, normalized
    system: str | None
    params: dict                                            # temperature, max_tokens, top_p, … (validated vs profile)
    tools: list[dict] = field(default_factory=list)        # JSON-schema tool defs
    tool_choice: str | dict | None = None
    response_format: dict | None = None                    # structured-output JSON schema, if the profile supports it
    reasoning: dict | None = None                          # {effort|budget} — only if profile.reasoning_control
    cache_hints: list[dict] = field(default_factory=list)  # prompt-cache breakpoints
    stream: bool = True


@dataclass(frozen=True)
class NormalizedUsage:
    # 02:65-72
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int = 0    # cache READS — priced at the discounted rate
    cache_write_tokens: int = 0     # cache SEEDING — priced at the cache_write premium
    reasoning_tokens: int = 0
    cost_usd: float = 0.0           # WE compute this (cost.py), not the provider/LiteLLM


class EventType(str, Enum):
    # 02:48-63 — the backend design streams a richer set (TEXT_DELTA/TOOL_CALL_*/STOP/ERROR); Phase 2
    # only needs text + a final usage event, so we pin the minimal pair and leave room to grow.
    TEXT = "text"
    USAGE = "usage"


@dataclass(frozen=True)
class NormalizedStreamEvent:
    # 02:58-63 — every provider streams these; payload keyed by type: TEXT→{"text"},
    # USAGE→{"usage": NormalizedUsage, "substitutions": [Substitution]} (cost attached by the caller).
    type: EventType
    data: dict


@dataclass(frozen=True)
class Substitution:
    # 02:216-221 — the capability leak, made visible (unused in Phase 1; present so the shape is pinned)
    feature: str     # "structured_output" | "forced_tool_choice" | "reasoning_control" | …
    action: str      # "dropped" | "emulated_via_prompt" | "downgraded"
    detail: str

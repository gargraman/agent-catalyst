"""Shared OpenAI-shaped adapter plumbing (M1). OpenAI + Ollama both receive LiteLLM's unified
(OpenAI-shaped) streaming response, so they share the stream loop and usage mapper here; Anthropic
reuses the loop with its own usage mapper (cache_read/cache_creation divergence — see usage-mapping).

Transport is reached via the module attribute `transport.stream_chunks` so tests can monkeypatch it.
Raw token counts only — cost is attached by the gateway (never here, never from litellm).
"""
from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, AsyncIterator, Callable

from runtime.providers import transport
from runtime.providers.ir import (
    EventType,
    NormalizedRequest,
    NormalizedStreamEvent,
    NormalizedUsage,
)
from runtime.providers.profiles import Substitution, preflight_substitutions

if TYPE_CHECKING:
    from runtime.providers.profiles import CapabilityProfile


def build_messages(req: NormalizedRequest) -> list[dict]:
    """System turn (if any) prepended to the normalized messages."""
    messages: list[dict] = []
    if req.system:
        messages.append({"role": "system", "content": req.system})
    messages.extend(req.messages)
    return messages


def _json_emulation_note(response_format: dict) -> str:
    """Instruction that emulates structured output on a model lacking native structured_output."""
    schema = response_format.get("json_schema", response_format)
    return (
        "You MUST respond with a single valid JSON value and nothing else — no prose, no markdown "
        "fences. It MUST conform to this JSON Schema:\n"
        + json.dumps(schema, separators=(",", ":"))
    )


def _forced_tool_name(tool_choice: dict) -> str | None:
    fn = tool_choice.get("function")
    if isinstance(fn, dict) and fn.get("name"):
        return fn["name"]
    return tool_choice.get("name")


def _tool_emulation_note(tool_choice: dict) -> str:
    """Instruction that emulates a forced tool call on a model lacking native forced_tool_choice."""
    name = _forced_tool_name(tool_choice)
    target = f"the tool named {name!r}" if name else "the required tool"
    return f"You MUST call {target} to produce your answer; do not reply with free-form text."


def build_wire_request(
    profile: "CapabilityProfile | None", req: NormalizedRequest
) -> tuple[list[dict], dict, list[Substitution]]:
    """Translate a NormalizedRequest into the (messages, params, substitutions) honoring the capability
    profile (contract obligation #6 / P7). Returns the runtime-discovered substitutions so the gateway can
    merge them with the preflight list.

    The capability-downgrade DECISION (which requested features the profile cannot honor) is taken from
    `profiles.preflight_substitutions` — the SINGLE authority — so the wire and the substitution ledger
    cannot drift. For each feature preflight flags as downgraded we OMIT it from the wire and, for the
    `emulated_via_prompt` rules (structured_output, forced_tool_choice), append an instruction to the
    system turn (so the recorded Substitution is never a lie). Features preflight does not flag are
    forwarded natively.

    Beyond capability gaps, this also records downgrades that are an M1 IMPLEMENTATION gap rather than a
    capability gap — a feature the profile SUPPORTS but this milestone does not yet forward on the wire —
    so they are never silent (the bug class F1/F2 from code review):
      * prompt-cache breakpoints: the wire mechanism is deferred to M7, so `cache_hints` on a
        caching-capable model is recorded `prompt_caching: dropped`.
      * token-budget reasoning: M1 maps only `effort`, so a `reasoning.budget` on a reasoning-capable
        model is recorded `reasoning_control: downgraded`.
    The caller still owns model routing + the tools list.
    """
    preflight = preflight_substitutions(profile, req) if profile is not None else []
    downgraded = {s.feature for s in preflight}
    messages = build_messages(req)
    params = dict(req.params)
    notes: list[str] = []
    extra: list[Substitution] = []

    # structured output ↔ response_format
    if req.response_format is not None:
        if "structured_output" in downgraded:
            notes.append(_json_emulation_note(req.response_format))   # emulated_via_prompt (per preflight)
        else:
            params["response_format"] = req.response_format

    # forced tool ↔ tool_choice. "forced_tool_choice" ∈ downgraded ⇒ a dict (forced) choice the profile
    # lacks (preflight's rule); a string ("auto"/"none") or a supported forced choice is forwarded as-is.
    if req.tool_choice is not None:
        if "forced_tool_choice" in downgraded:
            notes.append(_tool_emulation_note(req.tool_choice))
        else:
            params["tool_choice"] = req.tool_choice

    # reasoning control ↔ reasoning
    if req.reasoning is not None and "reasoning_control" not in downgraded:
        effort = req.reasoning.get("effort")
        if effort is not None:
            params["reasoning_effort"] = effort
        if "budget" in req.reasoning:
            extra.append(Substitution(
                "reasoning_control", "downgraded",
                "token-budget reasoning not forwarded in M1 (only effort is mapped)",
            ))

    # prompt caching: M1 never forwards cache breakpoints (wire mechanism deferred to M7). When the profile
    # SUPPORTS caching, preflight recorded nothing — so record the drop here so it is never silent (P7).
    if req.cache_hints and "prompt_caching" not in downgraded:
        extra.append(Substitution(
            "prompt_caching", "dropped", "cache breakpoints not forwarded on the wire until M7",
        ))

    if notes:
        note = "\n\n".join(notes)
        if messages and messages[0].get("role") == "system":
            head = messages[0]
            messages = [{**head, "content": f"{head['content']}\n\n{note}"}, *messages[1:]]
        else:
            messages = [{"role": "system", "content": note}, *messages]

    return messages, params, extra


def map_openai_usage(usage) -> NormalizedUsage:
    """LiteLLM Usage (OpenAI shape) → NormalizedUsage. prompt_tokens is inclusive of cached;
    completion_tokens inclusive of reasoning (matches cost.py conventions). Missing details → 0
    (honest absence)."""
    if usage is None:
        return NormalizedUsage(0, 0)
    ptd = getattr(usage, "prompt_tokens_details", None)
    ctd = getattr(usage, "completion_tokens_details", None)
    return NormalizedUsage(
        input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
        output_tokens=getattr(usage, "completion_tokens", 0) or 0,
        cached_input_tokens=(getattr(ptd, "cached_tokens", 0) or 0) if ptd is not None else 0,
        reasoning_tokens=(getattr(ctd, "reasoning_tokens", 0) or 0) if ctd is not None else 0,
    )


def map_anthropic_usage(usage) -> NormalizedUsage:
    """LiteLLM Anthropic Usage → NormalizedUsage (usage-mapping.md ⚠ divergence). Robust to BOTH shapes
    LiteLLM might emit (we cannot confirm which without a live call in M1 — so handle either; code review F3):

    * NATIVE Anthropic shape — `input_tokens` is EXCLUSIVE of cache; we add `cache_read_input_tokens` +
      `cache_creation_input_tokens` back so `input_tokens` is INCLUSIVE (our convention / cost.py).
    * OpenAI-NORMALIZED shape — `prompt_tokens` is already INCLUSIVE of cache (no `input_tokens` attr);
      we defer to `map_openai_usage` (reads `prompt_tokens_details.cached_tokens`) and add NO cache back.

    Detection is by the presence of the native `input_tokens` attribute. `reasoning_tokens` mapped if the
    response exposes an extended-thinking count, else 0 (honest absence). Missing usage → NormalizedUsage(0, 0).
    The canned chunks in test_anthropic_adapter.py pin BOTH shapes; a live run only needs to confirm which."""
    if usage is None:
        return NormalizedUsage(0, 0)
    if getattr(usage, "input_tokens", None) is None:
        # OpenAI-normalized shape (prompt_tokens inclusive) — no add-back.
        return map_openai_usage(usage)
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    base_input = getattr(usage, "input_tokens", 0) or 0
    return NormalizedUsage(
        input_tokens=base_input + cache_read + cache_creation,   # make inclusive (add cache back in)
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cached_input_tokens=cache_read,
        cache_write_tokens=cache_creation,
        reasoning_tokens=getattr(usage, "reasoning_tokens", 0) or 0,
    )


# Feature 023: outbound correlation header (platform-generated ids only — FR-014) and provider response id capture.
_PROVIDER_CORR = re.compile(r"^[0-9a-f]{32}$")
_PROVIDER_REQ_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
# litellm (1.106.6, spike T002) exposes provider response headers on each chunk's _hidden_params.
_REQ_ID_HEADERS = ("llm_provider-x-request-id", "llm_provider-request-id")


def _response_request_id(chunk) -> str | None:
    hp = getattr(chunk, "_hidden_params", None)
    headers = hp.get("additional_headers") if isinstance(hp, dict) else None
    if not isinstance(headers, dict):
        return None
    for name in _REQ_ID_HEADERS:
        val = headers.get(name)
        if isinstance(val, str) and _PROVIDER_REQ_ID.match(val):
            return val
    return None


async def stream_via_transport(
    *,
    model: str,
    messages: list[dict],
    api_key: str,
    params: dict,
    tools: list[dict] | None = None,
    usage_mapper: Callable[[object], NormalizedUsage] = map_openai_usage,
    substitutions: list[Substitution] | None = None,
    timeout: float | None = None,
    provider_correlation_id: str | None = None,
) -> AsyncIterator[NormalizedStreamEvent]:
    """Drive the LiteLLM transport seam and yield normalized events: TEXT_DELTA as content streams,
    then exactly one terminal USAGE carrying raw NormalizedUsage (cost attached by the gateway).
    `substitutions` are the runtime-discovered downgrades from build_wire_request (M1 wire-implementation
    gaps — cache/budget); the gateway merges these with the preflight substitutions from supports().
    Tool-call events are deferred to M2 (content only in M1)."""
    kwargs: dict = {"model": model, "messages": messages, "api_key": api_key, **params}
    if tools:
        kwargs["tools"] = tools
    if timeout is not None:
        # Bound the litellm call itself (research D5). Omitted when None so M1's existing behavior and its
        # mocked tests are unchanged; litellm accepts `timeout` as a request-level bound.
        kwargs["timeout"] = timeout
    if provider_correlation_id and _PROVIDER_CORR.match(provider_correlation_id):
        # Only a platform-generated 32-hex id is ever forwarded (023 FR-014); anything else is dropped, never an error.
        # Merge, don't overwrite: wire params may already carry provider headers.
        kwargs["extra_headers"] = {**(kwargs.get("extra_headers") or {}), "X-Correlation-Id": provider_correlation_id}

    usage: NormalizedUsage | None = None
    usage_seen = False
    provider_request_id: str | None = None
    async for chunk in transport.stream_chunks(**kwargs):
        if provider_request_id is None:
            provider_request_id = _response_request_id(chunk)
        choices = getattr(chunk, "choices", None) or []
        if choices:
            delta = getattr(choices[0], "delta", None)
            content = getattr(delta, "content", None) if delta is not None else None
            if content:
                yield NormalizedStreamEvent(EventType.TEXT_DELTA, {"text": content})
        chunk_usage = getattr(chunk, "usage", None)
        if chunk_usage is not None:
            usage = usage_mapper(chunk_usage)
            usage_seen = True

    # Emit USAGE only when the provider actually reported usage. A stream that ends with NO usage object
    # is degenerate (usage-mapping.md) — we do NOT synthesize a zero USAGE that would read as a success;
    # the gateway detects the missing USAGE and emits a degraded ERROR instead (P8).
    if usage_seen:
        data: dict = {"usage": usage, "substitutions": list(substitutions or [])}
        if provider_request_id:
            data["provider_request_id"] = provider_request_id
        yield NormalizedStreamEvent(EventType.USAGE, data)

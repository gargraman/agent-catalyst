"""Invoke request + typed SSE frame contracts (M4, feature 005; finding F9).

The frames are the wire re-encoding of `runtime.providers.ir.NormalizedStreamEvent`. Only the frame types the
M2a/M3 runtime emits today are modeled (tools/RAG/guardrail frames are later features). These models give the
OpenAPI doc a typed SSE schema; the actual bytes are produced by `api/sse.py`.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class InvokeIn(BaseModel):
    inputs: dict = Field(default_factory=dict)      # validated by the runtime against the version io_contract (P6)
    version_id: str | None = None                   # None → the agent's active_version_id


# ---- SSE frame payloads (event: <type>, data: <json>) --------------------------------------------
class RunStartFrame(BaseModel):
    run_id: str
    version: str
    model: str


class TextDeltaFrame(BaseModel):
    text: str


class ReasoningDeltaFrame(BaseModel):
    text: str


class ToolCallFrame(BaseModel):
    id: str
    name: str | None = None
    args: dict | None = None
    args_delta: str | None = None


class UsageFrame(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float | None = None
    cost_is_estimate: bool = False


class ErrorFrame(BaseModel):
    code: str
    message: str


class DoneFrame(BaseModel):
    run_id: str
    status: str


# Discriminated set for the OpenAPI doc (event name → payload).
SSE_FRAME_TYPES: dict[str, type[BaseModel]] = {
    "run_start": RunStartFrame,
    "text_delta": TextDeltaFrame,
    "reasoning_delta": ReasoningDeltaFrame,
    "tool_call_start": ToolCallFrame,
    "tool_call_delta": ToolCallFrame,
    "tool_call_end": ToolCallFrame,
    "usage": UsageFrame,
    "error": ErrorFrame,
    "done": DoneFrame,
}

SSEFrameName = Literal[
    "run_start", "text_delta", "reasoning_delta",
    "tool_call_start", "tool_call_delta", "tool_call_end",
    "usage", "error", "done",
]

"""Pydantic contracts for run history + metrics (feature 006, US5) — mirror data.ts Run + dashboard stats.

Run records are identity + metrics only (no raw content, SC-009). Metrics are computed server-side over the
workspace's runs (FR-015).
"""
from __future__ import annotations

import datetime

from pydantic import BaseModel, Field


class RunOut(BaseModel):
    id: str
    agent_id: str | None
    version_id: str | None
    model: str
    usage: dict
    latency_ms: int
    cost_usd: float = Field(description=(
        "Run cost in USD computed from provider-reported usage only (feature 025, FR-015): failed provider attempts "
        "that reported no usage are unpriced and disclosed through `retries` — this is not a claim of the exact "
        "provider charge."))
    outcome: str
    source: str
    correlation_id: str
    created_at: datetime.datetime
    retries: int = Field(default=0, description=(
        "Provider retries this run spent (transport attempts beyond the first, over model turns and embedding "
        "calls). Cost reflects reported usage only, so retried attempts with no reported usage add no cost."))


class SeriesOut(BaseModel):
    name: str
    values: list[float]


class DashboardOut(BaseModel):
    window_days: int
    buckets: list[str]          # ISO dates, oldest→newest
    spend: list[float]          # Σ cost_usd per day
    calls: list[int]            # count of playground+published runs per day (eval excluded)
    total_cost_usd: float
    total_calls: int


class RunsStatsOut(BaseModel):
    total: int
    by_outcome: dict            # outcome → count
    by_source: dict             # source → count


class EvalStatsOut(BaseModel):
    total_eval_runs: int
    by_outcome: dict

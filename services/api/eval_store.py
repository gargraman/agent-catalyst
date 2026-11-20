"""Feature 026 US4 — session-side reads/writes for persisted evals (data-model §eval_*).

The DECISIONS live in the pure `api.baseline`; this module only loads the rows it needs and writes the evidence.
"""
from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession

from api.baseline import ResolvedBaseline, resolve_baseline
from api.models import EvalBaseline, EvalResult, EvalRun
from evals.drift import EvalPolicy


@dataclass(frozen=True)
class SuiteBaseline:
    resolved: ResolvedBaseline
    frozen: EvalBaseline | None = None          # the pointer row when origin == "frozen" (frozen_by / frozen_at)


async def suite_baselines(session: AsyncSession, suite_ids: Iterable[uuid.UUID],
                          active_version_id) -> dict[uuid.UUID, SuiteBaseline]:
    """Resolve the baseline of each suite with `api.baseline.resolve_baseline` — the SAME rule a run uses (D7).

    SQL only narrows the candidates (one frozen pointer + the newest completed active-version run per suite, ordered
    exactly like `resolve_baseline`'s tie-break); the pure function still makes the choice."""
    ids = list(suite_ids)
    if not ids:
        return {}
    frozen = {b.suite_id: (b, r) for b, r in (await session.execute(
        select(EvalBaseline, EvalRun).join(EvalRun, EvalRun.id == EvalBaseline.eval_run_id)
        .where(EvalBaseline.suite_id.in_(ids)))).all()}
    newest: dict[uuid.UUID, EvalRun] = {}
    if active_version_id is not None:
        rows = (await session.execute(
            select(EvalRun).where(EvalRun.suite_id.in_(ids), EvalRun.status == "completed",
                                  EvalRun.version_id == uuid.UUID(str(active_version_id)))
            .ext(distinct_on(EvalRun.suite_id))
            .order_by(EvalRun.suite_id, EvalRun.finished_at.desc().nulls_last(), EvalRun.id.desc()))).scalars()
        newest = {r.suite_id: r for r in rows}
    out = {}
    for sid in ids:
        pointer, frun = frozen.get(sid, (None, None))
        auto = [newest[sid]] if sid in newest else []
        out[sid] = SuiteBaseline(resolve_baseline(frun, active_version_id, auto), pointer)
    return out


# ---- policy (decision b: stored camelCase by PUT /workspaces/settings, applied + recorded snake_case) -------------

POLICY_DEFAULTS: dict = {"min_cases": 10, "quality_threshold": 90.0, "efficiency_budget_pct": 10.0,
                         "allow_low_confidence_override": False}
_CAMEL = {"minCases": "min_cases", "qualityThreshold": "quality_threshold",
          "efficiencyBudgetPct": "efficiency_budget_pct", "allowLowConfidenceOverride": "allow_low_confidence_override"}


def _coerce(key: str, value):
    """Type-check one stored value; anything malformed falls back to the default (never a 500, never a looser gate
    by accident — a bad value cannot be read as a permissive one)."""
    default = POLICY_DEFAULTS[key]
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    if isinstance(default, int):
        return int(value) if float(value).is_integer() and value >= 1 else default
    return float(value) if value >= 0 else default


def applied_policy(raw: dict | None) -> dict:
    """The snake_case policy a run APPLIES: the workspace's camelCase settings merged over the defaults."""
    out = dict(POLICY_DEFAULTS)
    for camel, snake in _CAMEL.items():
        if isinstance(raw, dict) and camel in raw:
            out[snake] = _coerce(snake, raw[camel])
    return out


def to_engine_policy(applied: dict) -> EvalPolicy:
    return EvalPolicy(**applied)


# ---- evidence writes (T054): one transaction, never updated after insert ----------------------------------------

EVAL_CASE_CAP = 25   # owner O4: a bigger suite is refused (400), never silently truncated — at run AND authoring time

TIMEOUT_REASON = "A provider call hit its deadline mid-run, so no verdict was drawn. The run is recorded as failed."


async def persist_completed(session: AsyncSession, run: EvalRun, results: list[EvalResult]) -> EvalRun:
    """Insert the run + its per-case results on the caller's session (the request's single transaction)."""
    session.add(run)
    await session.flush()
    for r in results:
        r.eval_run_id, r.workspace_id = run.id, run.workspace_id
    session.add_all(results)
    await session.flush()
    await session.refresh(run)
    return run


async def persist_failed(session: AsyncSession, run: EvalRun) -> EvalRun:
    """A failed run is evidence too (E6): the row, no `eval_results` (its per-case `runs` rows are kept by the caller)."""
    run.status, run.verdict = "failed", None
    session.add(run)
    await session.flush()
    await session.refresh(run)
    return run

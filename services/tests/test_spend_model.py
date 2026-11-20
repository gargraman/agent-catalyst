"""Feature 009 (M6 durable spend cap) — pure spend-math helpers (T002).

`_month_start_utc` is pure (no DB). `month_to_date_spend` / `active_cap_usd` read the durable ledger +
settings (integration, compose Postgres). RED until `api.spend` exists.
"""
from __future__ import annotations

import datetime
from decimal import Decimal

import pytest

from api.db import tenant_session
from api.spend import DEFAULT_MONTHLY_CAP_USD, _month_start_utc, active_cap_usd, month_to_date_spend
from tests._api_helpers import seed_limits, seed_run, seed_workspace


def test_month_start_utc_is_first_of_month_midnight_naive():
    start = _month_start_utc(datetime.datetime(2026, 11, 7, 13, 45, 30))
    assert start == datetime.datetime(2026, 11, 1, 0, 0, 0)
    assert start.tzinfo is None  # naive — matches the `timestamp` (no tz) runs.created_at column


def test_default_cap_is_2000_mirroring_frontend_default():
    assert DEFAULT_MONTHLY_CAP_USD == Decimal("2000")


@pytest.mark.integration
async def test_month_to_date_spend_sums_only_this_month_for_this_workspace():
    ws = await seed_workspace()
    await seed_run(ws, cost_usd=1.5)
    await seed_run(ws, cost_usd=2.25)
    await seed_run(ws, cost_usd=100.0, created_at=datetime.datetime(2000, 1, 1))  # prior month → excluded
    other = await seed_workspace()
    await seed_run(other, cost_usd=500.0)                                         # other tenant → excluded
    async with tenant_session(ws) as s:
        mtd = await month_to_date_spend(s, ws)
    assert mtd == Decimal("3.75")


@pytest.mark.integration
async def test_active_cap_from_settings_else_default():
    ws = await seed_workspace()
    async with tenant_session(ws) as s:
        assert await active_cap_usd(s, ws) == DEFAULT_MONTHLY_CAP_USD   # no settings row → default
    await seed_limits(ws, 500)
    async with tenant_session(ws) as s:
        assert await active_cap_usd(s, ws) == Decimal("500")            # settings value wins

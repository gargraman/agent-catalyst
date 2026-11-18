"""Feature 023 US1 (T011) + US2 (T024): per-call log lines end-to-end through the API.

Integration (seeds versions) + mocked gateway/transport (no live/paid call). Captures the `ac.call_log` logger.
"""
from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from api import deps
from api.db import tenant_session
from api.models import Run
from api.settings import get_settings
from runtime.providers import gateway
from tests._api_helpers import client, dev_headers, eval_run_body, seed_agent, seed_api_token, seed_version, seed_workspace
from tests._evals_helpers import VALID_OUTPUT, make_routed_gateway
from tests._invoke_helpers import make_gateway, priced_usage, stop_ev, text_ev, usage_ev

pytestmark = pytest.mark.integration


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[dict] = []

    def emit(self, record):
        self.lines.append(json.loads(record.getMessage()))


@pytest.fixture
def cap(monkeypatch):
    monkeypatch.setenv("AC_TELEMETRY_LOG_CONTENT_IN_DEV", "false")   # default prod-like behavior: content OFF
    get_settings.cache_clear()
    h = _Capture()
    lg = logging.getLogger("ac.call_log")
    lg.addHandler(h)
    yield h
    lg.removeHandler(h)
    get_settings.cache_clear()


async def _seed():
    ws = await seed_workspace()
    aid = await seed_agent(ws)
    vid = await seed_version(ws, aid)
    return ws, aid, vid


async def _eval_body(ws: str, aid: str, vid: str, n: int = 3) -> dict:
    """026 T050: the suite lives on the server now; the body is only {version_id, suite_id}."""
    return await eval_run_body(ws, aid, vid, n)


def _bearer() -> str:
    return "ac_" + "live_" + "calllog_" + uuid.uuid4().hex[:10]


async def _runs(ws):
    async with tenant_session(ws) as s:
        return list((await s.execute(select(Run))).scalars().all())


async def test_four_callers_one_line_per_call_identical_shape(cap, monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("hi"), usage_ev(priced_usage(cost_usd=0.00042)), stop_ev()]))
    ws, aid, vid = await _seed()
    async with client() as c:
        # playground
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "hi"}})
        assert r.status_code == 200
        play = [x for x in cap.lines if x["workspace_id"] == ws]
        assert len(play) == 1 and play[0]["source"] == "playground" and play[0]["role"] == "candidate"
        # published
        bearer = _bearer()
        await seed_api_token(ws, aid, secret=bearer)
        r = await c.post(f"/v1/agents/{aid}/invoke", headers={"Authorization": "Bearer " + bearer},
                         json={"inputs": {"message": "hi"}})
        assert r.status_code == 200, r.text
        pub = [x for x in cap.lines if x["workspace_id"] == ws][1:]
        assert len(pub) == 1 and pub[0]["source"] == "published"

    # Σ line cost == runs.cost_usd for each run
    rows = await _runs(ws)
    assert len(rows) == 2
    by_run = {}
    for line in cap.lines:
        if line["workspace_id"] == ws:
            by_run.setdefault(line["run_id"], Decimal(0))
            by_run[line["run_id"]] += Decimal(line["cost_usd"])
    # The runs row's PK is DB-minted (not the runtime run_id), so reconcile per run by count + total.
    assert len(by_run) == len(rows)
    assert sum(by_run.values()) == sum(Decimal(r.cost_usd) for r in rows)

    # eval (candidate + judge)
    cap.lines.clear()
    monkeypatch.setattr(gateway, "stream", make_routed_gateway(
        candidate_cost=0.001, judge_body={"rubric": {"accuracy": 5.0}, "pairwise": "candidate", "rationale": "ok"}))
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs",
                         headers=dev_headers(workspace=ws, claims={ws: "publisher"}), json=await _eval_body(ws, aid, vid))
    assert r.status_code == 201
    ev = [x for x in cap.lines if x["workspace_id"] == ws]
    cand = [x for x in ev if x["role"] == "candidate"]
    judge = [x for x in ev if x["role"] == "judge"]
    assert len(cand) == 3 and len(judge) >= 1
    assert all(x["source"] == "eval" for x in ev)
    # identical key sets across all four caller types; no text by default (SC-008)
    keys = {frozenset(x) for x in play + pub + cand + judge}
    assert len(keys) == 1
    assert not ({"input_text", "output_text"} & set(next(iter(keys))))
    # candidate cost reconciles with the persisted eval runs
    eval_rows = [r for r in await _runs(ws) if r.source == "eval"]
    assert sum(Decimal(x["cost_usd"]) for x in cand) == sum(Decimal(r.cost_usd) for r in eval_rows)


async def test_content_opt_in_redacts_and_never_leaks_key(cap, monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway([text_ev("ok"), usage_ev(), stop_ev()]))
    ws, aid, _ = await _seed()
    seeded = f"my key {deps.PROVIDER_API_KEY} and " + "sk-ant-" + "api03-" + "K" * 30 + " mail jane.doe@example.com"
    async with client() as c:
        adm = dev_headers(workspace=ws, subject="adm", claims={ws: "admin"})
        # default OFF → no text
        await c.post(f"/api/v1/agents/{aid}/invoke", headers=adm, json={"inputs": {"message": seeded}})
        assert "input_text" not in cap.lines[-1]
        assert (await c.put("/api/v1/workspace/telemetry", headers=adm, json={"content_logging": True})).status_code == 200
        await c.post(f"/api/v1/agents/{aid}/invoke", headers=adm, json={"inputs": {"message": seeded}})
    line = cap.lines[-1]
    assert "input_text" in line and "output_text" in line
    raw = json.dumps(cap.lines)
    assert deps.PROVIDER_API_KEY not in raw
    assert "K" * 30 not in raw
    assert "jane.doe@example.com" not in raw
    assert "[REDACTED:email]" in line["input_text"]


# ---- US2 (T024): end-to-end correlation; only a platform id reaches the provider -------------------------
import re  # noqa: E402
from types import SimpleNamespace  # noqa: E402

from runtime.providers import transport  # noqa: E402

_HEX32 = re.compile(r"^[0-9a-f]{32}$")
_TP = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def _capture_transport(monkeypatch):
    calls: list[dict] = []

    async def _fake(**kwargs):
        calls.append(kwargs)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="hi"))], usage=None)
        yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None))],
                              usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2,
                                                    prompt_tokens_details=None, completion_tokens_details=None))
    monkeypatch.setattr(transport, "stream_chunks", _fake)
    return calls


@pytest.mark.parametrize("hdr, caller_id", [
    ({"X-Correlation-Id": "demo-123"}, "demo-123"),
    ({"traceparent": _TP}, "4bf92f3577b34da6a3ce929d0e0e4736"),
])
async def test_supplied_id_on_row_and_lines_never_outbound(cap, monkeypatch, hdr, caller_id):
    calls = _capture_transport(monkeypatch)
    ws, aid, _ = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers={**dev_headers(workspace=ws), **hdr},
                         json={"inputs": {"message": "hi"}})
    assert r.status_code == 200 and r.headers["X-Correlation-Id"] == caller_id
    rows = await _runs(ws)
    assert [row.correlation_id for row in rows] == [caller_id]
    lines = [x for x in cap.lines if x["workspace_id"] == ws]
    assert len(lines) == 1 and lines[0]["correlation_id"] == caller_id
    pid = lines[0]["provider_correlation_id"]
    assert _HEX32.match(pid) and pid != caller_id
    assert [c["extra_headers"]["X-Correlation-Id"] for c in calls] == [pid]
    assert caller_id not in repr(calls)


async def test_generated_id_consistent_everywhere(cap, monkeypatch):
    calls = _capture_transport(monkeypatch)
    ws, aid, _ = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                         json={"inputs": {"message": "hi"}})
    cid = r.headers["X-Correlation-Id"]
    assert _HEX32.match(cid)
    assert [row.correlation_id for row in await _runs(ws)] == [cid]
    line = [x for x in cap.lines if x["workspace_id"] == ws][0]
    assert line["correlation_id"] == line["provider_correlation_id"] == cid
    assert calls[0]["extra_headers"] == {"X-Correlation-Id": cid}


async def test_eval_run_shares_one_id_pair_across_candidate_and_judge(cap, monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_routed_gateway(
        candidate_cost=0.001, judge_body={"rubric": {"accuracy": 5.0}, "pairwise": "candidate", "rationale": "ok"}))
    ws, aid, vid = await _seed()
    async with client() as c:
        r = await c.post(f"/api/v1/agents/{aid}/eval-runs",
                         headers={**dev_headers(workspace=ws, claims={ws: "publisher"}), "X-Correlation-Id": "eval-7"},
                         json=await _eval_body(ws, aid, vid, n=3))
    assert r.status_code == 201
    lines = [x for x in cap.lines if x["workspace_id"] == ws]
    assert {x["role"] for x in lines} == {"candidate", "judge"}
    assert {x["correlation_id"] for x in lines} == {"eval-7"}
    pids = {x["provider_correlation_id"] for x in lines}
    assert len(pids) == 1 and _HEX32.match(next(iter(pids))) and "eval-7" not in pids


# ---- analysis G3: failure path through the API (both invoke planes) -----------------------------------------
from tests._invoke_helpers import error_ev  # noqa: E402


async def test_provider_failure_still_one_line_per_call_both_planes(cap, monkeypatch):
    monkeypatch.setattr(gateway, "stream", make_gateway(
        [error_ev({"type": "provider_error", "retryable": True, "message": "upstream 503"})]))
    ws, aid, _ = await _seed()
    bearer = _bearer()
    await seed_api_token(ws, aid, secret=bearer)
    async with client() as c:
        r1 = await c.post(f"/api/v1/agents/{aid}/invoke", headers=dev_headers(workspace=ws),
                          json={"inputs": {"message": "hi"}})
        r2 = await c.post(f"/v1/agents/{aid}/invoke", headers={"Authorization": "Bearer " + bearer},
                          json={"inputs": {"message": "hi"}})
    assert r1.status_code == r2.status_code == 200
    lines = [x for x in cap.lines if x["workspace_id"] == ws]
    assert [x["source"] for x in lines] == ["playground", "published"]
    assert all(x["success"] is False and x["error_code"] == "provider_error" for x in lines)
    assert all(x["cost_usd"] == "0" and x["cost_known"] is False for x in lines)
    rows = await _runs(ws)
    assert sorted(r.outcome for r in rows) == ["provider_error", "provider_error"]

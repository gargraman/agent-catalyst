"""H2 — provider abstraction experiment (Phase 2 of the viability MVP).

One normalized request → two maximally-different providers (hosted gpt-5-mini + local Ollama), both
streaming. For each: compute cost from OUR capability profile, reconcile the paid provider's cost to an
independent hand-calc within ±5%, and surface EVERY capability downgrade as a Substitution (none silent).

The finding this produces: is the "one request drives any provider" abstraction leaky, and if so, is the
leak visible (a recorded Substitution) rather than silent? See the printed table + substitution list.
"""

import asyncio
import dataclasses
import time
from pathlib import Path

from cost import compute_cost
from ir import EventType, NormalizedRequest, NormalizedUsage, Substitution
from log import log_call
from profiles.loader import resolve_profile
from providers.base import adapter_for

# anchor the log to mvp/logs/ regardless of the cwd the CLI is launched from
_LOG_PATH = Path(__file__).resolve().parent.parent / "logs" / "h2.jsonl"
_COST_TOLERANCE = 0.05  # ±5%, locked before running (plan §2)

# H2 runtime pair. Judge model (gpt-5) is deliberately NOT here — it's Phase-3+ only.
_TARGETS = [("openai", "gpt-5-mini"), ("ollama", "llama3.1:8b")]

# --- The ONE synthetic mock-incident triage request (fabricated — safe for hosted APIs) -------------
_INCIDENT = (
    "ALERT 2026-10-22 03:14 UTC — Host web-prod-07: outbound connections to 185.220.101.0/24 "
    "(known Tor exit range) spiked 40x over baseline. EDR flagged a new process `kworkerd` writing to "
    "/tmp and reading ~/.aws/credentials. No prior change ticket. Two failed sudo attempts logged "
    "90s before the process started. Host serves the public checkout API."
)

_ESCALATE_TOOL = {
    "type": "function",
    "function": {
        "name": "escalate",
        "description": "Escalate the incident to the on-call responder.",
        "parameters": {
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                "reason": {"type": "string"},
            },
            "required": ["severity", "reason"],
        },
    },
}


def build_request() -> NormalizedRequest:
    """One normalized request; only (provider, model) are re-targeted per adapter (dataclasses.replace).

    reasoning + cache_hints are set on purpose so the Ollama profile (which lacks both) MUST record
    substitutions; temperature is non-default so the OpenAI gpt-5 adapter must drop+record it at runtime.
    """
    return NormalizedRequest(
        provider="",            # set per-target below
        model="",
        api_version=None,
        messages=[{"role": "user", "content": _INCIDENT}],
        system=(
            "You are a SOC triage assistant. Classify the incident and respond with ONLY a JSON object "
            'with exactly these keys: {"severity": one of low|medium|high|critical, '
            '"summary": one sentence, "next_step": one concrete action}.'
        ),
        params={"temperature": 0.2, "max_tokens": 3000},
        tools=[_ESCALATE_TOOL],
        tool_choice=None,                       # tool included but not forced (keeps JSON text output)
        response_format={"type": "json_object"},
        reasoning={"effort": "low"},            # Ollama lacks reasoning_control → substitution
        cache_hints=[{"type": "ephemeral"}],    # Ollama lacks prompt_caching → substitution
        stream=True,
    )


def _hand_calc_cost(profile, u: NormalizedUsage) -> float:
    """GENUINELY INDEPENDENT re-derivation of the cost — the ±5% reconciliation check.

    This must NOT share cost.py's code path: it is written as a separate per-group sum over the published
    per-Mtok rates, and it derives the billable output on its own (carving reasoning OUT of the inclusive
    completion count). If cost.py regressed to double-counting reasoning, this hand-calc would diverge and
    the ±5% assertion would fire — which is the whole point of the cross-check (not a tautology)."""
    p = profile.pricing
    per_mtok = 1.0 / 1_000_000.0

    # Rates as published (per Mtok); reasoning/cached/cache_write fall back to their base side.
    rate_input = p["input"]
    rate_cached = p.get("cached_input", p["input"])
    rate_cache_write = p.get("cache_write", p["input"])
    rate_output = p["output"]
    rate_reasoning = p.get("reasoning", p["output"])

    # Token groups, each priced once. Output is INCLUSIVE of reasoning, so bill (output - reasoning) at the
    # output rate and reasoning separately — derived here independently of cost.py.
    non_reasoning_output = max(0, u.output_tokens - u.reasoning_tokens)
    fresh_input = u.input_tokens - u.cached_input_tokens - u.cache_write_tokens

    dollars = 0.0
    dollars += fresh_input * rate_input * per_mtok
    dollars += u.cached_input_tokens * rate_cached * per_mtok
    dollars += u.cache_write_tokens * rate_cache_write * per_mtok
    dollars += non_reasoning_output * rate_output * per_mtok
    dollars += u.reasoning_tokens * rate_reasoning * per_mtok
    return dollars


async def _run_one(provider: str, model: str, base_req: NormalizedRequest, api_key: str) -> dict:
    profile = resolve_profile(provider, model)
    adapter = adapter_for(provider)
    req = dataclasses.replace(base_req, provider=provider, model=model)

    subs: list[Substitution] = list(adapter.supports(profile, req))  # preflight downgrades
    text_parts: list[str] = []
    usage = NormalizedUsage(0, 0)

    t0 = time.perf_counter()
    async for ev in adapter.stream(req, api_key=api_key, profile=profile):
        if ev.type is EventType.TEXT:
            text_parts.append(ev.data["text"])
        elif ev.type is EventType.USAGE:
            usage = ev.data["usage"]
            subs.extend(ev.data.get("substitutions", []))  # runtime-discovered downgrades
    latency_ms = (time.perf_counter() - t0) * 1000.0

    cost = compute_cost(profile, usage)
    output = "".join(text_parts).strip()

    log_call(
        _LOG_PATH, model=f"{provider}/{model}", prompt_version="h2-triage-v1",
        input=_INCIDENT, output=output, latency_ms=latency_ms, cost_usd=cost, success=bool(output),
    )
    return {
        "provider": provider, "model": model, "profile": profile,
        "output": output, "usage": usage, "cost": cost, "latency_ms": latency_ms, "subs": subs,
    }


async def run_h2(*, api_key: str) -> list[dict]:
    base_req = build_request()
    results = [await _run_one(p, m, base_req, api_key) for p, m in _TARGETS]
    _print_report(results)
    _assert_findings(results)
    return results


def _print_report(results: list[dict]) -> None:
    print("\n" + "=" * 78)
    print("H2 — provider abstraction: one NormalizedRequest → two providers (streaming)")
    print("=" * 78)
    for r in results:
        snippet = " ".join(r["output"].split())
        if len(snippet) > 220:
            snippet = snippet[:220] + "…"
        print(f"\n--- {r['provider']}/{r['model']} ---")
        print(f"  output : {snippet or '(no text content)'}")
    print("\n" + "-" * 78)
    hdr = f"{'provider/model':<24}{'in':>7}{'out':>7}{'cached':>8}{'reason':>8}{'cost_usd':>12}{'subs':>6}"
    print(hdr)
    print("-" * 78)
    for r in results:
        u = r["usage"]
        print(
            f"{r['provider'] + '/' + r['model']:<24}"
            f"{u.input_tokens:>7}{u.output_tokens:>7}{u.cached_input_tokens:>8}"
            f"{u.reasoning_tokens:>8}{r['cost']:>12.8f}{len(r['subs']):>6}"
        )
    print("-" * 78)

    print("\nSubstitutions recorded (the capability leak, made visible):")
    for r in results:
        if not r["subs"]:
            print(f"  {r['provider']}/{r['model']}: (none)")
        for s in r["subs"]:
            print(f"  {r['provider']}/{r['model']}: {s.feature} → {s.action} — {s.detail}")

    # Cost reconciliation (paid provider only; Ollama is $0 so its hand-calc is trivially 0).
    print("\nCost reconciliation vs independent hand-calc (±5% tolerance):")
    for r in results:
        if r["cost"] == 0.0:
            print(f"  {r['provider']}/{r['model']}: $0.00 (local, no pricing to reconcile)")
            continue
        hand = _hand_calc_cost(r["profile"], r["usage"])
        delta = abs(r["cost"] - hand) / hand if hand else 0.0
        print(
            f"  {r['provider']}/{r['model']}: compute_cost=${r['cost']:.8f}  "
            f"hand=${hand:.8f}  delta={delta * 100:.4f}%  "
            f"{'OK' if delta <= _COST_TOLERANCE else 'FAIL'}"
        )
    print()


def _assert_findings(results: list[dict]) -> None:
    by = {r["provider"]: r for r in results}

    # H2 bar: cost within ±5% of hand-calc for every paid provider.
    for r in results:
        if r["cost"] > 0.0:
            hand = _hand_calc_cost(r["profile"], r["usage"])
            delta = abs(r["cost"] - hand) / hand if hand else 0.0
            assert delta <= _COST_TOLERANCE, (
                f"{r['provider']}/{r['model']} cost delta {delta:.4%} exceeds ±{_COST_TOLERANCE:.0%}"
            )

    # Every downgrade must be surfaced as a Substitution (none silent). The Ollama run is guaranteed to
    # downgrade reasoning (profile has reasoning_control=false and the request sets a reasoning hint).
    ollama_features = {s.feature for s in by["ollama"]["subs"]}
    assert "reasoning_control" in ollama_features, (
        "Ollama dropped the reasoning hint without recording a Substitution — SILENT LEAK"
    )

    # Both providers must have produced usage (streaming returned a usage event).
    for r in results:
        assert r["usage"].output_tokens > 0, f"{r['provider']}/{r['model']} produced no output tokens"

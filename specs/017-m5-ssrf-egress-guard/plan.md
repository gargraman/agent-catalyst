# Implementation Plan: M5 — SSRF / Egress Guard (F19)

**Branch**: `feat/backend-m0-foundation` (feature dir `017-m5-ssrf-egress-guard`) | **Date**: 2026-11-11 | **Spec**: [spec.md](./spec.md)

**Input**: `specs/017-m5-ssrf-egress-guard/spec.md` · **Parent umbrella**: [`015-m5-security-completion`](../015-m5-security-completion/) (shared design: [research R3](../015-m5-security-completion/research.md), [data-model](../015-m5-security-completion/data-model.md))

## Summary

Build the P1 **app-level egress guard** (`api/egress.py`) that every outbound HTTP tool call must pass before
a byte leaves the process: enforce scheme (http/https), a **per-tool domain allow-list** (deny-by-default),
and an **always-on IP block-set** (private/loopback/link-local/ULA/multicast/reserved/unspecified + cloud
metadata `169.254.169.254` / `fd00:ec2::254`, IPv4 **and** IPv6 incl. IPv4-mapped). It **validates every
resolved IP** (multi-A split-horizon), re-validates **every redirect hop** through the same guard (no internal
redirects), and exposes a **connected-IP check** so the executor pins the dial to a validated address and
re-checks it (DNS-rebind / TOCTOU defense). Everything **fails closed** (FR-027). Refusals raise
`EgressBlocked` carrying ONLY a coarse `reason` (no IP/URL/secret — FR-009) and increment
`ac_egress_blocked_total{reason}`.

**Key reuse / deltas found in code:**
- **No tool EXECUTION exists** — the runtime only *validates* a model's tool call and surfaces it unexecuted
  (`test_invoke_tool_enforcement::test_valid_tool_call_surfaced_and_not_executed`); there is no outbound socket
  for a tool anywhere. The egress sandbox/gVisor proxy is P2 (`09 §10`). ⇒ Ship the guard as a **pure,
  independently-tested library with a concrete integration seam**, exactly as the task + research R3 direct.
- `AppError`/envelope + the F12 refusal-counter pattern (`metrics.record_refusal`) already exist → add a leaf
  `EgressBlocked(AppError)` and a `record_egress_blocked(reason)` counter mirroring that pattern.
- Per-tool allow-list already has a seed shape: `tool_binding.config_snapshot["egress"]` is a domain list
  (`tests/conftest._tool_binding`) → `allowlist_for_tool()` reads it; no schema change (tool exec is P2).
- `ipaddress` stdlib classifies every reserved range (incl. IPv6 ULA via `is_private`, IPv4-mapped via
  `.ipv4_mapped`) → no new dependency; DNS is an **injectable resolver** so tests are deterministic/offline.

## Technical Context

**Language/Version**: Python 3.12 (`services/`)
**Primary Dependencies**: stdlib only (`ipaddress`, `socket`, `urllib.parse`) — **no new runtime dep**; pytest for tests
**Storage**: none — egress policy is **config, not a table** (per-tool allow-list + global block-set); no Postgres/Redis
**Testing**: pytest with an injected resolver (no real DNS, no sockets) — fully deterministic
**Project Type**: Web service (`services/api`); guard lives in `api/`, NOT `services/runtime/` (P6 purity)
**Performance Goals**: one DNS resolve + O(#IPs) range checks per call; negligible
**Constraints**: deterministic, **fail-closed** (FR-027); no secret/IP/URL in client errors or logs (FR-009, P5); `services/runtime/` untouched (P6)
**Scale/Scope**: the P1 validator; the network sandbox/egress proxy remains **P2** (out of scope)

## Constitution Check

*GATE: pre-Phase 0 + re-checked post-Phase 1.*

| Principle | Status | Notes |
|-----------|--------|-------|
| P1 outcome | ✅ | A prompt-injected agent cannot become an SSRF/exfil primitive; measurable SC-003. No LLM. |
| P2 scope/phases | ✅ | P1 app-level guard only; network sandbox explicitly deferred to P2 (not dropped). |
| **P3 deterministic fail-closed** | ✅ | Pure server-side validator; unresolvable/ambiguous/error ⇒ refuse. **No deviation** (unlike 016's limiter, a security control has no fail-open path). |
| P4 evidence | ✅ (N/A judge) | No model/prompt behavior; deterministic TDD only. |
| P5 data/tenant | ✅ | Client error carries only a coarse `reason`; no IP/URL/query/secret in errors, logs, or metric labels. |
| P6 reproducible | ✅ | `services/runtime/` untouched; guard is in `api/`; no snapshot/version change. |
| P7 providers/cost | ✅ | No provider contract/cost change. |
| P8 operate/visible | ✅ | Every refusal counted on `ac_egress_blocked_total{reason}` + a WARNING log (host only). |
| P9 test-first | ✅ | Red→Green→Refactor; focused `pytest tests/test_egress_ssrf.py`; no live calls. |

**Gate: PASS** (no deviations).

## Design

### Components

- **`api/egress.py`** (NEW): the guard.
  - `ip_block_reason(ip) -> str|None` — coarse block reason (metadata/loopback/link_local/multicast/
    unspecified/private/reserved/denylisted) or None; unwraps IPv4-mapped IPv6; honors operator extra-deny CIDRs.
  - `validate_url(url, *, allowlist_domains, resolver) -> EgressTarget` — the pre-flight: scheme → allow-list
    (deny-by-default) → resolve → block-check **every** IP → return the pinned IPs. Literal-IP URLs are
    block-checked first (no DNS).
  - `validate_connected_ip(ip)` — rebind/TOCTOU last line: check the address actually dialed.
  - `validate_redirect(location, *, base_url, allowlist_domains, resolver)` — re-run the full guard on each hop.
  - `allowlist_for_tool(config_snapshot)` — the per-tool allow-list seam (`config_snapshot["egress"]`).
  - `EgressTarget` dataclass (scheme/host/port/ips) — the pinned dial target the executor connects to.
- **`api/errors.py`** CHANGE: add `EgressBlocked(AppError)` → `502 egress.blocked`; client envelope carries only
  `detail={"reason": ...}` (FR-009).
- **`api/metrics.py`** CHANGE: add `ac_egress_blocked_total{reason}` + `record_egress_blocked()` (best-effort,
  never flips a block to an allow), mirroring `record_refusal`.
- **`api/settings.py`** CHANGE: `egress_extra_deny_cidrs` (CSV env, `NoDecode`, validated-at-load, additive-only
  to the always-on baseline). The baseline cannot be disabled — a security control, so (unlike 016's limiter) it
  does **not** key off any enable flag.

### Pipeline position / integration seam

The guard is the gate the **future P1 HTTP-tool executor** calls before dialing (seam documented at the foot of
`egress.py`): `allowlist_for_tool` → `validate_url` → dial **pinned** to `target.ips[0]` with redirects
**disabled** → `validate_connected_ip(peer)` → `validate_redirect` per hop. A refusal is fed back to the model
as a sanitized tool-error (model never learns the internal target). **Status: library built + fully tested;
NOT wired** — tool execution does not exist in P1 (same pattern as other not-yet-wired seams).

## TDD mapping (acceptance → Red test → command)

| Acceptance / FR | Red test (in `tests/test_egress_ssrf.py`) | Command |
|-----------------|-------------------------------------------|---------|
| AS1 / FR-006 | literal private/loopback/link-local/metadata/unspecified refused | `::test_literal_internal_ip_refused` |
| SC-003 | blocked literal never reaches the resolver (zero bytes) | `::test_literal_ip_block_precedes_dns_no_bytes_sent` |
| FR-008 | allow-listed host resolving to a private/metadata IP refused | `::test_allowlisted_host_resolving_private_is_refused` / `::..._metadata_...` |
| Edge (multi-A) | one public + one private A record → refused | `::test_multi_a_one_private_refused` |
| Edge (IPv6) | ULA/link-local/loopback + IPv4-mapped blocked | `::test_ipv6_ranges_blocked` |
| FR-027 | bad scheme / no host / DNS failure → fail closed before dial | `::test_bad_scheme_or_host_fails_closed_before_dns`, `::test_unresolvable_host_fails_closed` |
| FR-008 | deny-by-default + lookalike rejection + suffix match | `::test_empty_allowlist_denies_even_public`, `::test_allowlist_rejects_lookalikes`, `::test_allowlist_suffix_match_allows_subdomain` |
| AS2 / FR-007 | redirect to metadata / internal-resolved / off-allow-list refused | `::test_redirect_to_metadata_is_refused`, `::..._internal_resolved_host_...`, `::..._off_allowlist_...` |
| AS3 / FR-008 | connected-IP rebind check blocks the dialed private IP | `::test_connected_ip_rebind_blocked` |
| AS4 / FR-009 | refusal leaks no IP/URL/query/secret; metric increments | `::test_refusal_is_non_leaking`, `::test_block_increments_metric` |
| settings | operator extra-deny CIDR blocks an otherwise-public IP | `::test_extra_deny_cidr_blocks_otherwise_public_ip` |
| seam | `allowlist_for_tool` reads the per-tool config shape | `::test_allowlist_for_tool` |

Regression: full `pytest -q` (no behavior change to existing routes — purely additive). Lint/type: `ruff check . && mypy .`.

## Project Structure

```text
services/
├── api/
│   ├── egress.py        # NEW (SSRF/egress validator — pure library + integration seam)
│   ├── errors.py        # CHANGE EgressBlocked(AppError) → 502 egress.blocked
│   ├── metrics.py       # CHANGE ac_egress_blocked_total{reason} + record_egress_blocked
│   └── settings.py      # CHANGE egress_extra_deny_cidrs (additive-only, validated at load)
└── tests/
    └── test_egress_ssrf.py  # NEW (injected resolver — deterministic, offline)
```

**Structure Decision**: a small single-purpose module in `api/` (never `runtime/` — P6). DNS is injected so the
engine is deterministic and the same calls serve the P2 MCP/sandbox path unchanged.

## Complexity Tracking

| Deviation | Why needed | Simpler alternative rejected |
|-----------|-----------|------------------------------|
| Guard ships **unwired** (library + seam only) | Tool EXECUTION does not exist in P1 (runtime surfaces tool calls unexecuted; egress sandbox is P2) — there is no call site to wire yet | Faking a tool-exec path to "wire" it would invent P2 surface early and couple the guard to a shape not yet designed; the documented seam + `allowlist_for_tool` make wiring a drop-in when the executor lands |

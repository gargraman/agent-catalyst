# Feature Specification: M5 — SSRF / Egress Guard (F19)

**Feature Branch**: `feat/backend-m0-foundation` (feature dir `017-m5-ssrf-egress-guard`)

**Created**: 2026-11-11

**Status**: Draft · **Priority**: P1 · **Parent**: [`015-m5-security-completion`](../015-m5-security-completion/spec.md) (M5 umbrella)

**Input**: Split from the M5-completion epic (sub-feature 015b). Closes un-built **High**-severity finding **F19**.

> **Shared design**: [research R3](../015-m5-security-completion/research.md) ·
> [data-model (egress policy)](../015-m5-security-completion/data-model.md).
> **Dependencies**: none — independent, can run in parallel with 016.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A prompt-injected agent cannot become an SSRF / exfiltration primitive (Priority: P1)

A knowledge-base document or tool result hides an instruction steering an agent to call an HTTP tool against an
internal address (cloud metadata, internal service, private IP). The platform refuses any tool egress resolving
to a private/link-local/loopback destination, and refuses a redirect from an allowed public domain to an
internal host — before the P2 egress sandbox exists.

**Why this priority**: F19 is an un-built High-severity hole and the single control that keeps a prompt-injected
agent from becoming an SSRF/exfiltration primitive (Constitution P3).

**Independent Test**: Configure an HTTP tool allow-list, then attempt egress to (a) a private IP, (b) an allowed
domain redirecting to an internal host, (c) a hostname resolving to a private IP (rebind); assert each is
refused before any outbound request leaves.

**Acceptance Scenarios**:

1. **Given** an HTTP tool with a public-domain allow-list, **When** the agent calls a private/loopback/
   link-local/metadata address, **Then** the call is refused with a non-leaking error and nothing is sent.
2. **Given** a request to an allowed domain, **When** it redirects to an internal host, **Then** the redirect is
   not followed and the call is refused.
3. **Given** a hostname on the allow-list, **When** it resolves (or re-resolves) to a private IP, **Then** the
   connection is refused (validated against the address actually connected to).
4. **Given** a refused egress attempt, **When** logged, **Then** the refusal is recorded without exposing
   secrets or full internal target detail to a client-visible error.

### Edge Cases

- A domain resolves to multiple A records (one public, one private) — validate the IP actually dialed, not just
  the first record.
- IPv6 private/ULA/link-local ranges and IPv4-mapped IPv6 must also be blocked.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-006**: Refuse outbound HTTP tool egress to private, loopback, link-local, and cloud-metadata addresses,
  validated against the address actually connected to.
- **FR-007**: Do not follow a redirect from an allowed domain to an internal/disallowed host.
- **FR-008**: Validate the resolved IP (not only the hostname) to defeat DNS rebinding; enforce the per-tool
  domain allow-list.
- **FR-009**: Record a refused egress for diagnostics without leaking secrets or sensitive internal target
  detail to client-visible errors.
- **FR-027** (cross-cutting): Enforced in deterministic server-side code; fails closed on error.

### Key Entities

- **Egress policy** (config, not a table): per-tool `allowlist_domains`; global IP blocklist (private/loopback/
  link-local/ULA/metadata, IPv4+IPv6); evaluated at call time against the resolved + connected IP.

## Success Criteria *(mandatory)*

- **SC-003**: 100% of attempted egress to private/internal/metadata addresses (direct, via redirect, via
  rebinding) is refused, with zero bytes sent to the blocked target.

## Assumptions

- This is the P1 **app-level** guard; the network sandbox/gVisor egress proxy remains **P2** (out of scope).
- Default posture: deny all private/reserved ranges; the exact metadata/range list is finalized at tasks.
- Source: `docs/backend/09 §10`, finding F19 in `docs/implementation-plan.md`.

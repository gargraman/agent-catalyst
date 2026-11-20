---
description: "Task list — 017 M5 SSRF / Egress Guard (F19)"
---

# Tasks: M5 — SSRF / Egress Guard (F19)

**Input**: Design documents from `/specs/017-m5-ssrf-egress-guard/` + shared design in `/specs/015-m5-security-completion/`
**Prerequisites**: spec.md, plan.md, 015 research.md (R3), 015 data-model.md (egress policy)
**Tests**: REQUIRED — Constitution P9 mandates test-first (Red→Green→Refactor). All paths under `services/`.

**Single user story (US1).** MVP = US1 = the whole feature. Independent of 016 (no shared Redis; egress policy is
config, not a table). The guard ships as a **pure library + documented integration seam** — tool EXECUTION does
not exist in P1 (runtime surfaces tool calls unexecuted; the egress sandbox is P2), so there is no live call
site to wire (plan Complexity Tracking).

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no incomplete deps)
- Paths are repo-relative; run commands from `services/`.

---

## Phase 1: Setup (Shared Infrastructure)

- [x] T001 No new dependency — confirm `ipaddress`/`socket`/`urllib.parse` (stdlib) cover the block-set + resolve + redirect parsing (research R3); nothing to add to `services/pyproject.toml`.

---

## Phase 2: Foundational (Blocking Prerequisites)

**⚠️ Scaffolding only — no tested behavior yet (keeps Red evidence valid per P9).**

- [x] T002 Add `EgressBlocked(AppError)` to `services/api/errors.py` → `502 egress.blocked`; client envelope carries only `detail={"reason": ...}` (no IP/URL/secret — FR-009).
- [x] T003 [P] Add `ac_egress_blocked_total{reason}` + `record_egress_blocked(reason)` to `services/api/metrics.py` (best-effort; never flips a block to an allow — mirror `record_refusal`).
- [x] T004 [P] Add `egress_extra_deny_cidrs` to `services/api/settings.py`: CSV env (`Annotated[list[str], NoDecode]`), split + **validate-at-load** (bad CIDR crashes startup, not a request — fail-closed config); additive-only to the always-on baseline (no disable flag — security control).

**Checkpoint**: error class, counter, and settings import cleanly; the baseline block-set has no off switch.

---

## Phase 3: User Story 1 — SSRF / egress guard (Priority: P1) 🎯 MVP

**Goal**: Every outbound HTTP tool call is refused fail-closed when it targets a private/loopback/link-local/
multicast/metadata address (direct, resolved, multi-A, IPv6, IPv4-mapped), is off the per-tool allow-list,
redirects to an internal/off-allow-list host, or re-resolves/dials a blocked IP (rebind) — with zero bytes sent.

**Independent Test**: With an allow-list configured, attempt egress to (a) a private/metadata IP, (b) an allowed
domain redirecting to an internal host, (c) a hostname resolving to a private IP — each refused before any dial.

### Tests for User Story 1 (write FIRST, confirm they FAIL on behavior — not import) ⚠️

- [x] T005 [US1] `tests/test_egress_ssrf.py` scaffold + a deterministic injected resolver + a `_never_resolver` dial-proxy (asserts no DNS/dial on pre-DNS refusals) so Red tests fail on behavior, not import.
- [x] T006 [US1] Red `test_literal_internal_ip_refused` + `test_literal_ip_block_precedes_dns_no_bytes_sent`: literal private/loopback/link-local/metadata/unspecified refused, block fires BEFORE the resolver (SC-003 zero-bytes) (FR-006, AS1).
- [x] T007 [US1] Red `test_allowlisted_host_resolving_private_is_refused` / `..._metadata_...`: an allow-listed host resolving to a blocked IP is refused — validate the resolved IP, not the hostname (FR-008).
- [x] T008 [US1] Red `test_multi_a_one_private_refused` + `test_ipv6_ranges_blocked`: multi-A (public+private) refused; IPv6 ULA/link-local/loopback + IPv4-mapped blocked (spec Edge Cases).
- [x] T009 [US1] Red `test_bad_scheme_or_host_fails_closed_before_dns` + `test_unresolvable_host_fails_closed`: non-http(s) scheme, missing host, and DNS failure all fail closed before any dial (FR-027).
- [x] T010 [US1] Red allow-list tests: `test_empty_allowlist_denies_even_public` (deny-by-default), `test_allowlist_rejects_lookalikes` (`notexample.com`, `example.com.evil`), `test_allowlist_suffix_match_allows_subdomain`, `test_public_target_passes_and_returns_pinned_ips` (FR-008).
- [x] T011 [US1] Red redirect tests: `test_redirect_to_metadata_is_refused`, `test_redirect_to_internal_resolved_host_refused`, `test_redirect_off_allowlist_refused`, `test_redirect_relative_against_base_stays_allowed` — every hop re-validated through the same guard (FR-007, AS2).
- [x] T012 [US1] Red `test_connected_ip_rebind_blocked` + `test_connected_ip_public_ok`: the connected-IP check refuses the address actually dialed (DNS-rebind/TOCTOU) (FR-008, AS3).
- [x] T013 [US1] Red `test_refusal_is_non_leaking` + `test_block_increments_metric`: refusal carries only a coarse `reason` (no IP/URL/query/secret) and increments the counter (FR-009, AS4).
- [x] T014 [US1] Red `test_extra_deny_cidr_blocks_otherwise_public_ip` + `test_allowlist_for_tool`: operator extra-deny CIDR blocks an otherwise-public IP; `allowlist_for_tool` reads the per-tool `config_snapshot["egress"]` shape (FR-008 seam).

### Implementation for User Story 1 (make the above Green, in dependency order)

- [x] T015 [US1] Implement `ip_block_reason()` in `services/api/egress.py`: metadata set + loopback/link_local/multicast/unspecified/private(ULA)/reserved via `ipaddress`; unwrap IPv4-mapped IPv6; apply `_extra_deny_networks()` from settings.
- [x] T016 [US1] Implement `validate_url()`: scheme guard → literal-IP block-check (no DNS) → allow-list (deny-by-default, suffix match) → inject-resolve → block-check EVERY IP → return `EgressTarget` with pinned IPs. Depends on T015.
- [x] T017 [US1] Implement `validate_connected_ip()` (rebind last line) and `validate_redirect()` (urljoin against base → re-run `validate_url`). Depends on T016.
- [x] T018 [US1] Implement `allowlist_for_tool()` (reads `config_snapshot["egress"]` list / `{allow_domains:[...]}` / str; empty ⇒ deny-by-default) and `_refuse()` (record metric + WARNING log with host only, then raise `EgressBlocked`). Depends on T002, T003.

### Refactor (keep all US1 tests green)

- [x] T019 [US1] Document the integration seam at the foot of `egress.py` (executor call order: `allowlist_for_tool`→`validate_url`→pin-dial→`validate_connected_ip`→`validate_redirect`); confirm no `services/runtime/` import and no FastAPI/DB import (P6, pure library).

**Checkpoint**: US1 fully functional and independently testable — `pytest tests/test_egress_ssrf.py` green (46).

---

## Phase 4: Polish & Cross-Cutting

- [x] T020 Run full regression `pytest -q` — purely additive; existing routes unchanged (617 passed, 1 skipped).
- [ ] T021 [P] `ruff check . && mypy .` clean. *(Not run: ruff/mypy are not installed in the local venv; defer to CI.)*
- [x] T022 [P] Secret/target-scan check: assert no IP/URL/query/secret appears in the `EgressBlocked` envelope or the metric label (only a coarse `reason`) (FR-009 / P5) — covered by `test_refusal_is_non_leaking`.
- [ ] T023 At DoD: update tracker memory `m5-security-split` (017 impl ✅) and reconcile the F19 / egress row in `docs/backend-integration-status.md` + the M5 note in `docs/implementation-plan.md` (per the update-docs-after-feature rule). *(Deferred — owner to confirm before docs reconciliation.)*

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002–T004)** → **US1 (T005–T019)** → **Polish (T020–T023)**.
- Within US1: **tests (T005–T014) before implementation (T015–T018)**; T015 → T016 → T017; T018 needs T002/T003; refactor T019 last.
- T002 (EgressBlocked) + T003 (counter) block T018 going green.

### Parallel Opportunities

- T003 ∥ T004 (different files) after T002.
- Red tests T006–T014 are all in **one file** → write sequentially after the T005 scaffold.
- T021 ∥ T022 in Polish.

---

## Implementation Strategy

Single-story MVP: Setup + Foundational, then drive US1 strictly test-first (Red evidence before Green, per P9),
then Polish. Keep `services/runtime/` untouched (P6). No real DNS/sockets anywhere — an injected resolver covers
the resolve, multi-A, rebind, and redirect paths deterministically.

## Notes

- The guard is **unwired by design** — tool execution is P2 (no outbound tool socket exists in P1). The
  `allowlist_for_tool` helper + the documented seam make wiring a drop-in when the HTTP-tool executor lands.
- Unlike 016's limiter (availability control, fail-open), this is a security control: it has **no fail-open
  path** and the baseline block-set has **no disable flag** — fail closed everywhere (FR-027 / P3).

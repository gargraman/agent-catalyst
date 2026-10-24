# Backend Low-Level Design (`docs/backend/lld/`)

> The layer **beneath** `docs/backend/`. Where `docs/backend/NN-*.md` is the implementation-level
> **design** (DDL, interfaces, the argued decisions), this folder is the **buildable** detail a developer
> with 3–10 yrs experience picks up to write the module without re-deriving anything: full typed
> signatures, the algorithm bodies the parent left as `...`, exception classes, state-machine tables,
> validation rules, concurrency, and the exact frontend-contract parity.

## The one hard rule — reference, don't restate

Each LLD doc **only fills the gaps** its parent left open. Anything the parent already specifies — the DDL
in `01`, `compute_cost` in `02`, the graph edges in `03` — is **linked** (`see 01 §4.2`), never re-pasted.
Every LLD doc opens with a **"What this adds over parent NN"** delta list so you can see, at a glance, what
is new here versus what lives upstream. This keeps the LLD layer from silently forking into a second copy
of `docs/backend/` that drifts out of sync.

## How to read a pair

Read the parent first for *intent and decisions*, then the `.lld.md` for *the exact types to type out*.
`01-data-model.md` tells you **why** versions are immutable snapshots; `01-data-model.lld.md` gives you the
actual `VersionSnapshot` dataclass and the canonical hash that makes immutability real in code.

## Status

Built one subtask at a time; each is verified (cross-refs, frontend parity, snippet-compile) before the
next starts. See `/Users/raman.garg/.claude/plans/stateful-snacking-pond.md` for the full backlog + the
foundation dependency map.

| # | LLD doc | Parent | Tier | Status |
|---|---|---|---|---|
| 1 | [`01-data-model.lld.md`](01-data-model.lld.md) — data model & shared contracts | `01` | P0 (gate) | **done** |
| 2 | [`00-conventions.lld.md`](00-conventions.lld.md) — exceptions, pagination, idempotency | `00`/`08` | P0 | **done** |
| 3 | [`02-provider-gateway.lld.md`](02-provider-gateway.lld.md) | `02` | P1 (core #1) | **done** |
| 4 | [`05-eval-drift.lld.md`](05-eval-drift.lld.md) | `05` | P1 (core #2) | **done** |
| 5 | [`03-runtime.lld.md`](03-runtime.lld.md) | `03` | P1 (spine) | **done** |
| 6 | [`08-api-surface.lld.md`](08-api-surface.lld.md) | `08` | P2 | **done** |
| 7 | [`06-guardrails.lld.md`](06-guardrails.lld.md) | `06` | P2 | **done** |
| 8 | [`07-cost-cogs.lld.md`](07-cost-cogs.lld.md) | `07` | P2 | **done** |
| 9 | [`04-rag.lld.md`](04-rag.lld.md) | `04` | P3 | **done** |
| 10 | [`09-security-tenancy.lld.md`](09-security-tenancy.lld.md) — tenancy GUC, RLS DDL, per-user DEK (C4), org-plane boundary (C4) | `09` | P1 | **done** |
| 11 | [`10-observability-monitoring.lld.md`](10-observability-monitoring.lld.md) — four-sink spans, Langfuse trace, spec-A2 log line, redaction | `10` | P2 | **done** |
| 12 | [`11-deployment.lld.md`](11-deployment.lld.md) — process boundaries, shared DB layer, boot & migrations | `11` | P2 | **done** |
| 13 | [`12-channels.lld.md`](12-channels.lld.md) — inbound messaging adapter (the 4th caller of `invoke`) | `12` | P2 | **done** |
| 14 | [`13-branding.lld.md`](13-branding.lld.md) — branding / white-labeling (**intentional stub**, plain config) | `13` | P2 | **done (stub)** |
| 15 | [`14-adoption-enforcement.lld.md`](14-adoption-enforcement.lld.md) — enforcement modes, shadow-usage measurement, exemptions | `14` | P0 | **done** |
| 16 | [`15-org-governance-reporting.lld.md`](15-org-governance-reporting.lld.md) — admin-plane cross-tenant read, `SECURITY DEFINER` report fns | `15` | P0 | **done** |
| 17 | [`16-notifications.lld.md`](16-notifications.lld.md) — in-app record vs external delivery, prefs, reliable fan-out | `16` | P0 | **done** |
| 18 | [`17-mcp-identity.lld.md`](17-mcp-identity.lld.md) — per-user MCP identity (delegated connected accounts) | `17` | P2+ | **done** |
| 19 | [`18-identity-adapters.lld.md`](18-identity-adapters.lld.md) — IdP-agnostic authN → `Principal` | `18` | P1 | **done** |

## Conventions shared across every LLD doc

- **Python**: 3.12, `from __future__ import annotations` in every module so forward refs are free. Pydantic
  **v2** for anything validated at a boundary (jsonb column payloads, API DTOs); frozen `@dataclass` for
  the runtime-internal immutable contracts (`VersionSnapshot`, `RunContext`) that take no untrusted input.
- **Naming** follows `00 §8`: tables `snake_case` plural, money `numeric(12,6)` / `_usd`, tokens `int` /
  `_tokens`, timestamps `timestamptz` / `_at`. Python funcs `snake_case`, classes `PascalCase`.
- **Flagged deltas, never silent edits.** When an LLD doc must correct or extend a parent (e.g. a field the
  parent's type is missing), it states it in a **`> ⚠️ Amendment to NN`** callout rather than editing the
  parent in the same pass.

> **Reconciliation status (2026-10-24).** The `⚠️ Amendment` callouts raised across these LLDs **have been
> folded into `docs/backend/`** (`00`,`01`,`02`,`03`,`04`,`05`,`08`) and recorded in the `scope.md` decision
> log. The callouts remain in each LLD as the **finding + rationale trail** (they describe what was wrong and
> why it changed) — so wording like "not edited in the parent" is now **historical**; the fix is in the parent.
> New amendments raised by later subtasks (`06`,`07`,`04` LLDs) are folded as each lands.

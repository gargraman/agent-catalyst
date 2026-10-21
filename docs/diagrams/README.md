# Architecture diagrams

Standalone, interactive HTML diagrams (inline SVG, dark/light, generated with the `archify` skill).
Open any `.html` directly in a browser. The `.json` beside it is the editable source.

**Caveats for readers:** these are authored against the **working tree** (the schema docs and `frontend/`
are dirty), so they reflect current working-tree design, not a committed revision. There are no
machine-verified `sources` links — key file paths and `docs/backend/*` sections are cited in each diagram's
"Source" card instead. Several dense diagrams (the ERDs, the provider gateway) are rendered at `standard`
quality; the rest at `showcase`.

Regenerate one after editing its `.json`:

```bash
node <archify>/bin/archify.mjs finalize <type> <file>.json <file>.html --quality <standard|showcase> --json
```

## backend-design/ — the designed (not-yet-built) backend (`docs/backend/00–17`)

| Diagram | Type | Covers |
|---|---|---|
| `runtime-invoke-pipeline` | workflow | The one shared `runtime.invoke` core — guardrails → retrieve → model loop (00 §1/§4, 03, 06) |
| `provider-gateway` | workflow | IR, adapters, retry/fallback/breaker, **cost from our profiles** (02, 07) |
| `eval-drift-gate` | workflow | Two-layer scoring, verdict vs frozen baseline, 409 publish gate (05) |
| `version-lifecycle` | lifecycle | Agent-version states and the eval-gated `publishable` pivot (05, spec B10) |
| `published-invoke-lifecycle` | sequence | Token auth → RLS pin → spend → Vault key → invoke → Run (00 §5B, 09, 02) |
| `rag-ingestion-retrieval` | dataflow | Ingest → chunk → embed → pgvector; invoke-time retrieval (04) |
| `channel-ingress` | sequence | 4th caller: verify → federate → ack-fast → worker invoke → reply (12) |
| `mcp-per-user-connect` | sequence | **PROPOSED** per-user MCP via in-chat OAuth connect (doc 17) |
| `backend-deployment-plane` | architecture | Deployable units, state ownership, isolation boundaries (00 §2, 11, 09) |
| `observability-telemetry` | dataflow | Per-call log → Postgres / Langfuse / Prometheus; ops vs owner (10) |
| `owner-notifications` | dataflow | Triggers → emit + dedupe → recipient fan-out → inbox (16) |
| `adoption-enforcement` | workflow | Register → dedup (pgvector) → one funnel; the key-custody spine (14) |
| `org-governance-reporting` | sequence | Platform-admin 4th plane, one SECURITY DEFINER cross-tenant read, audited (15) |
| `branding-org-config` | architecture | Organization ≠ workspace; deploy-time brand resolution (13) |
| `schema-core-authoring` | erd | Data model pt.1 — identity, agents/versions, knowledge, skills, evals (01) |
| `schema-runtime-governance` | erd | Data model pt.2 — tools/MCP, publish, runs, ops, adoption (01) + proposed MCP identity |

## frontend-flows/ — the mock-only Next.js console

`console-architecture` (overview) · `app-boot-access-gate` · `agent-lifecycle-build-publish` ·
`playground-invoke-access` · `knowledge-ingestion` · `eval-drift-gate` · `channel-publish-secrets` ·
`mcp-registration-tool-binding` · `skill-authoring-signing`.

## Next steps

- **Per-user MCP identity (doc 17) — lock the proposal.** Fold the DDL (`mcp_servers.auth_mode`,
  `mcp_user_connections`) into `01-data-model.md` and the connect/callback routes into `08-api-surface.md`;
  build P2.a console connect → P2.b in-chat link → P2.c Entra/Okta OBO; add paused-run resume (v2).
  Decide eval handling for `per_user` tools (service connection vs. excluded + low-confidence).
- **Remaining optional depth (only two docs lack a dedicated diagram):** cost / COGS — BudgetGuard
  reserve→reconcile + the caching layers (07); guardrail stages & streaming sliding-buffer redaction (06).
  Both are partially covered inside `runtime-invoke-pipeline` / `provider-gateway` today.
- **Consolidation:** eval/drift is covered by `backend-design/eval-drift-gate` + the `frontend-flows` mock —
  keep the two layers distinct in their titles so they don't read as duplicates.
- **Commit:** the whole set is new/untracked on `main` — commit on a branch to pin it (the diagrams reflect
  the working tree, not a committed revision).

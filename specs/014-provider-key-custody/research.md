# Research & Decisions — Provider-key custody (feature 014)

No open `NEEDS CLARIFICATION`: the scope forks (AWS target, pluggable backend, provider-keys-only, full doc sweep, encryption approach) were resolved with the project owner during brainstorming before planning. This file records the decisions and rationale.

## R1 — Secrets backend: AWS Secrets Manager, behind a pluggable seam

- **Decision**: Raw secrets live in **AWS Secrets Manager** in deployed environments; the access to it is abstracted behind a provider-agnostic `SecretsProvider` interface so the cloud can be swapped later.
- **Rationale**: The deployment target is an AWS account (owner decision). SM gives managed encryption-at-rest (KMS CMK) and versioning without us writing custody crypto. The pluggable seam honors the owner's explicit "keep it swappable" requirement and mirrors the proven feature-012 `api/auth/` pattern.
- **Alternatives considered**: HashiCorp Vault Transit (the original design) — rejected: not the deployment target, and LocalStack doesn't emulate it. App-level KMS envelope encryption (GenerateDataKey + app-held ciphertext) — rejected for v1: more custody code to get right; SM's managed at-rest encryption suffices behind the seam (recorded as a non-goal).

## R2 — Provider split: `local` (in-process) + `aws` (boto3), config-selected

- **Decision**: Two interchangeable providers selected by `AC_SECRETS_PROVIDER` (`local` | `aws`), built by a cached `build_secrets_provider(settings)` in `api/secrets/registry.py`:
  - **`local`** — in-process store; for the pytest **unit** suite and offline dev. May seed from the existing `PROVIDER_API_KEY` env in **non-prod only** (dev continuity).
  - **`aws`** — boto3 Secrets Manager client; works against **LocalStack** locally (via `AWS_ENDPOINT_URL`) and real AWS in prod. boto3 is imported **lazily** by this module only (mirrors `runtime.providers.base.adapter_for` lazy import), so the unit suite never imports boto3.
- **Rationale**: Unit tests must run without a container or cloud; the real code path must still be exercisable locally. Using `aws`+LocalStack as the *compose* default means local dev drives the same boto3 path as prod, while `local` keeps unit tests hermetic.
- **Local-dev persistence note**: the `local` provider is in-process/ephemeral; a captured key won't survive a process restart. Therefore **compose sets `AC_SECRETS_PROVIDER=aws` against LocalStack** (persists for the container session). `local` is for unit tests + offline-no-compose dev.
- **Fail-closed default**: `AC_SECRETS_PROVIDER` defaults to `local`; the non-prod `PROVIDER_API_KEY` fallback in resolution is disabled when `AC_ENV=prod` (mirrors the 012 `env`-gated dev-auth provider), so an unconfigured prod process fails closed.

## R3 — `vault_path` semantics + secret naming

- **Decision**: `provider_keys.vault_path` is an **opaque reference** the client never interprets:
  - `aws`: the Secrets Manager **ARN** returned by `create_secret`.
  - `local`: a `local://{workspace_id}/{logical_id}` style handle.
- **Secret naming scheme (aws)**: `ac/{env}/{workspace_id}/provider-key/{provider_key_id}` (configurable prefix via `AC_SECRETS_PREFIX`), so secrets are namespaced per environment + workspace + key and never collide across tenants.
- **Rationale**: Keeping `vault_path` opaque preserves the FE `CredentialRef.vaultPath` contract unchanged and lets the backend swap cloud providers without a client-visible change.

## R4 — Resolve-at-invoke integration point + provider derivation

- **Decision**: Add `async def resolve_provider_key(session, ws, provider) -> str` to `api/provider_keys.py`. It selects the workspace's **active** key for `provider` (deterministic order — see R8), reads `vault_path`, and calls `provider_backend.get(vault_path)`. `invoke.py` and `evals.py` call it **after** the version snapshot is loaded (the agent's provider comes from the snapshot's model/profile config) and pass the result into `RunContext.api_key` — the exact seam `PROVIDER_API_KEY` uses today (`deps.py:31`).
- **Fail-closed**: no active key, or backend unavailable → raise a config error (`config_error`) **before** any provider call; in **non-prod only**, fall back to `PROVIDER_API_KEY` for dev continuity (disabled in prod → FR-014).
- **Rationale**: Resolving after the snapshot load keeps the P6 boundary intact (api resolves plaintext; runtime just receives the string) and reuses the existing access-gate ordering from feature 013.
- **No caching in v1**: rotation/revoke must take effect immediately; an indexed DB read + one SM `get` per run is cheap. A short-TTL cache can be added later if needed.

## R5 — Migration 0007 + RLS

- **Decision**: New Alembic migration `0007_provider_keys.py`: create a `provider_key_status` PG enum (`active`,`expiring`,`revoked`); create `provider_keys` (see data-model.md); `UNIQUE(workspace_id, label)`; enable **RLS** with the same `app.workspace_id` GUC policy used by other workspace-scoped tables (pattern from migrations 0002/0005) and grant the `ac_app` role. Downgrade drops table + enum.
- **Rationale**: Defense-in-depth tenancy (P5) consistent with every other resource; the superuser/migration role bypasses RLS, the request path (`ac_app`) is bound by it.

## R6 — boto3 dependency + LocalStack wiring

- **Decision**: Add `boto3` to the `services/pyproject.toml` `api` optional-deps extra. Add a `localstack` service to `deploy/compose.yaml` (Secrets Manager + KMS), and wire `AC_SECRETS_PROVIDER=aws`, `AC_AWS_ENDPOINT_URL=http://localstack:4566`, `AC_AWS_REGION`, and dummy static AWS creds for the `api` + `migrate` dev containers. The opt-in `test_aws_secrets_localstack.py` is marked `integration` + a dedicated opt-in guard (like the live-IAM test) so the default unit run doesn't need the container.
- **Rationale**: Exercises the real boto3 path locally without a cloud account; keeps the default test matrix hermetic.

## R7 — Masking reuse

- **Decision**: The server computes the masked value (recognizable head/tail, body hidden) mirroring `frontend/src/lib/secrets.ts` `maskSecret` (6 head + bullets + 4 tail; full bullets for short values). Masking is a pure helper in `api/provider_keys.py`.
- **Rationale**: Drop-in parity with the FE display; the client never derives the mask (server is source of truth).

## R8 — Deterministic multi-key resolution (FR-016)

- **Decision**: When a workspace has more than one `active` key for the same provider, resolution picks the **most-recently-added active** key (`ORDER BY created_at DESC, id DESC LIMIT 1`).
- **Rationale**: Deterministic and intuitive (the latest key an admin added wins); documented rather than arbitrary.

## R9 — Rotate / revoke semantics against the backend

- **Decision**:
  - **Rotate**: `aws` → `put_secret_value` (new version) on the same ARN; `local` → overwrite the handle's value. Update `masked` + `rotated_at`; keep id/label/status. Superseded value no longer resolves (we always read the current version).
  - **Revoke**: mark the row `revoked` (tombstone, retained) **and** disable the secret in the backend — `aws` → `delete_secret` (scheduled deletion; `get` then fails) ; `local` → drop the handle. Resolution of a revoked key fails closed.
- **Rationale**: Matches the FE semantics (rotate replaces, revoke tombstones) while ensuring the backend can no longer hand out the old/revoked secret.

## R10 — Full Vault→AWS doc sweep

- **Decision**: Reconcile **all** docs that name "Vault" to the pluggable-secrets/AWS model (owner chose full sweep). Load-bearing: `docs/scope.md` (a **decision-log entry** recording the reversal + why — required by P2/Governance), `docs/backend/{00-overview, 01-data-model §4.3/§4.6, 09-security-tenancy, 11-deployment, 18-identity-adapters}`; plus the ~40 remaining mentions across `docs/backend/**`, `docs/*.md`, and the diagram sources. Also update `docs/backend-integration-status.md` (provider-keys row → BUILT) and the status-tracker seam count (253→257 drift noted earlier). This is the DoD docs task; docs-only changes use link/consistency/render checks, not fabricated unit tests (P9).
- **Rationale**: The design docs currently assert HashiCorp Vault; leaving them stale would violate the "backend is source of truth / keep docs reconciled" standing instruction and the Governance source hierarchy.

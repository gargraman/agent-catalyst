# Data Model — Provider-key custody (feature 014)

## Entity: `provider_keys` (new table, migration 0007)

Workspace-scoped, RLS-protected. Holds **no raw secret** — only a masked display value, an opaque backend reference, and metadata. Mirrors `frontend/src/lib/data.ts` `ProviderKey` (drop-in) and `docs/backend/01-data-model.md §4.3`.

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | `uuid` PK | no | `server_default gen_random_uuid()` |
| `workspace_id` | `uuid` FK → `workspaces.id` | no | indexed; RLS discriminator (`WorkspaceScoped`) |
| `provider` | `text` | no | e.g. `Anthropic`, `OpenAI`, `Google`, `Mistral` — matches the agent's provider for resolution |
| `label` | `text` | no | human label; **`UNIQUE(workspace_id, label)`** |
| `masked` | `text` | no | server-computed display value (head/tail); never the raw secret |
| `vault_path` | `text` | no | **opaque** backend reference (AWS Secrets Manager ARN for `aws`; `local://…` handle for `local`) |
| `region` | `text` | yes | optional, provider-dependent (e.g. Vertex/Bedrock) |
| `status` | `provider_key_status` enum | no | `active` \| `expiring` \| `revoked`; default `active` |
| `created_at` | `timestamptz` | no | `server_default now()` |
| `rotated_at` | `timestamptz` | yes | set on rotate; null until first rotation |

**Enum**: `provider_key_status = ('active','expiring','revoked')` (PG-native, created in 0007, `create_type` managed by the migration — consistent with `agent_status`/`tool_kind`).

**Constraints & indexes**
- `UNIQUE(workspace_id, label)` — FR-006.
- Index on `workspace_id` (via `WorkspaceScoped`) + a resolution index on `(workspace_id, provider, status)` to serve `resolve_provider_key` cheaply.

**RLS** (migration 0007, pattern from 0002/0005)
- `ENABLE ROW LEVEL SECURITY` + a `USING`/`WITH CHECK` policy keyed on `workspace_id = current_setting('app.workspace_id')::uuid`.
- `GRANT SELECT, INSERT, UPDATE ON provider_keys TO ac_app` (no DELETE — revoke is an UPDATE to a tombstone; rows are retained for audit). The superuser/migration role bypasses RLS.

**State transitions**
```
(capture) ──► active ──(rotate)──► active        (same row; masked + rotated_at updated)
                 │
                 └──(revoke)──► revoked           (tombstone; secret disabled in backend; terminal)
```
`expiring` is a display status the FE models (e.g. for near-expiry keys); this feature writes `active`/`revoked` on its own actions and preserves any externally-set `expiring` value — it is NOT a gate for resolution (only `active` resolves).

**Resolution rule (FR-016)**: `SELECT … WHERE workspace_id = :ws AND provider = :provider AND status = 'active' ORDER BY created_at DESC, id DESC LIMIT 1`. Only `active` resolves; `revoked`/`expiring` do not.

## Interface: `SecretsProvider` (new `api/secrets/base.py`)

Provider-agnostic custody contract — the single seam all call sites use. API-layer only (P6: never imported by `runtime/`).

```
class SecretsProvider(Protocol):
    id: str                                  # "local" | "aws"
    def put(workspace_id, logical_id, value) -> str     # store; returns opaque vault_path
    def get(vault_path) -> str                           # fetch plaintext; raises SecretNotFound / SecretsUnavailable
    def rotate(vault_path, value) -> str                 # replace value; returns vault_path (may be unchanged)
    def disable(vault_path) -> None                      # revoke: make the secret unresolvable
```

- `put` / `rotate` / `disable` / `get` are **async** (match the async request/job path; boto3 calls run in a threadpool via `anyio.to_thread` / `run_in_executor` inside the aws provider so they don't block the event loop).
- Errors (new in `api/secrets/base.py`): `SecretsError` (base) → `SecretNotFound` (ref missing / revoked), `SecretsUnavailable` (backend unreachable). The custody layer maps both to a client-safe **`config_error`** at resolve time (fail-closed, FR-014); none carries the raw value (FR-017).

### Concrete providers
- **`LocalProvider`** (`local_provider.py`): in-process dict keyed by `vault_path`; `put` mints `local://{workspace_id}/{logical_id}/{uuid}`. Non-prod only: if asked to `get` a handle it doesn't have and `AC_ENV != prod`, the custody layer (not the provider) may fall back to `PROVIDER_API_KEY` — see resolution.
- **`AwsProvider`** (`aws_provider.py`): boto3 `secretsmanager` client (`endpoint_url=AC_AWS_ENDPOINT_URL` when set → LocalStack; default AWS otherwise). `put` → `create_secret(Name=ac/{env}/{ws}/provider-key/{id}, SecretString=value)`, returns ARN. `get` → `get_secret_value`. `rotate` → `put_secret_value` (new version). `disable` → `delete_secret` (scheduled). boto3 imported lazily by this module only.

### Registry (`api/secrets/registry.py`)
`build_secrets_provider(settings) -> SecretsProvider`, cached singleton (like `get_settings`), selecting `local`/`aws` from `settings.secrets_provider`; lazily imports `aws_provider` only when selected (keeps boto3 out of the unit run).

## Settings additions (`api/settings.py`)
| Field | Alias | Default | Notes |
|---|---|---|---|
| `secrets_provider` | `AC_SECRETS_PROVIDER` | `local` | `local` \| `aws` |
| `aws_endpoint_url` | `AC_AWS_ENDPOINT_URL` | `None` | set to LocalStack URL locally; unset → real AWS |
| `aws_region` | `AC_AWS_REGION` | `us-east-1` | SM client region |
| `secrets_prefix` | `AC_SECRETS_PREFIX` | `ac` | secret-name namespace root |
| ~~`vault_addr`~~ | ~~`AC_VAULT_ADDR`~~ | — | **removed** (Vault→AWS sweep) |

## Contract parity (frontend, drop-in)
`ProviderKeyOut` MUST serialize to the FE `ProviderKey` shape: `{ id, provider, label, masked, region?, added, rotatedAt?, status }` (`added` ← `created_at`, `rotatedAt` ← `rotated_at`). No `vault_path` is exposed to the client. No raw value is ever returned.

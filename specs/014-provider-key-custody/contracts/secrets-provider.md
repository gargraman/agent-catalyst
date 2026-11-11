# Contract — Internal `SecretsProvider` interface (feature 014)

The provider-agnostic custody seam. Lives in `api/secrets/` (API layer only — **never imported by `runtime/`**, P6). Mirrors the feature-012 `api/auth/` shape (Protocol in `base.py`, config-selected singleton in `registry.py`, one module per concrete provider).

## Protocol (`api/secrets/base.py`)

```python
@runtime_checkable
class SecretsProvider(Protocol):
    id: str   # "local" | "aws"

    async def put(self, workspace_id: str, logical_id: str, value: str) -> str:
        """Store `value`; return an OPAQUE vault_path reference. Raises SecretsUnavailable on backend failure."""

    async def get(self, vault_path: str) -> str:
        """Return the plaintext for `vault_path`. Raises SecretNotFound (missing/revoked) or SecretsUnavailable."""

    async def rotate(self, vault_path: str, value: str) -> str:
        """Replace the stored value; return the vault_path (may be unchanged). Raises SecretsUnavailable."""

    async def disable(self, vault_path: str) -> None:
        """Make the secret unresolvable (revoke). Idempotent. Raises SecretsUnavailable on backend failure."""


class SecretsError(Exception): ...
class SecretNotFound(SecretsError): ...      # ref missing or revoked
class SecretsUnavailable(SecretsError): ...  # backend unreachable
```

- All methods **async**; the `aws` provider runs blocking boto3 calls off the event loop (threadpool).
- No method accepts or returns anything that could place a raw secret in a log/trace — callers never log `value` or the returned plaintext (FR-017).

## Registry (`api/secrets/registry.py`)

```python
@lru_cache(maxsize=1)
def build_secrets_provider() -> SecretsProvider:
    s = get_settings()
    if s.secrets_provider == "aws":
        from api.secrets.aws_provider import AwsProvider   # lazy — keeps boto3 out of the unit run
        return AwsProvider(endpoint_url=s.aws_endpoint_url, region=s.aws_region, prefix=s.secrets_prefix, env=s.env)
    return LocalProvider()
```
- Selection is config-only (FR-002): switching backends changes no call-site code (SC-006).
- Fail-closed: an unknown `secrets_provider` value raises at build time.

## Concrete providers

### `LocalProvider` (`api/secrets/local_provider.py`)
- In-process dict keyed by `vault_path = local://{workspace_id}/{logical_id}/{uuid4}`.
- `put`/`rotate` set the value; `disable`/revoke drop it; `get` raises `SecretNotFound` when absent.
- Non-prod `PROVIDER_API_KEY` dev-continuity fallback is applied by the **custody layer** (resolution), not inside the provider, so the provider stays a clean store.

### `AwsProvider` (`api/secrets/aws_provider.py`)
- boto3 `secretsmanager` client; `endpoint_url` → LocalStack locally, default → real AWS.
- `put` → `create_secret(Name=f"{prefix}/{env}/{workspace_id}/provider-key/{logical_id}", SecretString=value)`; `vault_path` = returned **ARN**.
- `get` → `get_secret_value(SecretId=vault_path)["SecretString"]`; maps `ResourceNotFoundException`/pending-deletion → `SecretNotFound`, client/endpoint errors → `SecretsUnavailable`.
- `rotate` → `put_secret_value(SecretId=vault_path, SecretString=value)` (new version); returns the same ARN.
- `disable` → `delete_secret(SecretId=vault_path, RecoveryWindowInDays=…)` (scheduled deletion → `get` subsequently fails).
- boto3 is imported at module top; the module itself is imported lazily by the registry only when `secrets_provider=aws`.

## P6 purity guard
`test_runtime_purity_secrets.py` imports `runtime` and asserts no module under `runtime/` transitively imports `boto3` or `api.secrets` (extends the existing runtime-purity check that bans FastAPI/HTTP). The resolved plaintext reaches the runtime **only** as the `RunContext.api_key` string.

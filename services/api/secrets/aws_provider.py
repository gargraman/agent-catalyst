"""AWS Secrets Manager secrets provider (feature 014).

Stores raw secrets in AWS Secrets Manager (managed KMS encryption at rest). Works against LocalStack locally
(via `endpoint_url`) and real AWS in prod (no endpoint override + an IAM role). `vault_path` is the secret ARN.
boto3 is imported at module top; the module itself is imported LAZILY by the registry only when
`AC_SECRETS_PROVIDER=aws`, so the unit suite never imports boto3 (and runtime/ never sees it — P6).

Blocking boto3 calls run off the event loop via `asyncio.to_thread` so they don't stall the async request path.
"""
from __future__ import annotations

import asyncio

import boto3
from botocore.config import Config as _BotoConfig
from botocore.exceptions import BotoCoreError, ClientError

from api.secrets.base import SecretNotFound, SecretsUnavailable


class AwsProvider:
    """A `SecretsProvider` over AWS Secrets Manager."""

    id = "aws"

    def __init__(self, *, endpoint_url: str | None, region: str, prefix: str, env: str) -> None:
        self._prefix = prefix
        self._env = env
        # Short, bounded retries/timeouts so a backend outage fails closed quickly rather than hanging a run.
        self._client = boto3.client(
            "secretsmanager",
            endpoint_url=endpoint_url or None,
            region_name=region,
            config=_BotoConfig(retries={"max_attempts": 2, "mode": "standard"},
                               connect_timeout=3, read_timeout=5),
        )

    def _name(self, workspace_id: str, logical_id: str) -> str:
        return f"{self._prefix}/{self._env}/{workspace_id}/provider-key/{logical_id}"

    async def put(self, workspace_id: str, logical_id: str, value: str) -> str:
        name = self._name(workspace_id, logical_id)
        try:
            resp = await asyncio.to_thread(self._client.create_secret, Name=name, SecretString=value)
            return resp["ARN"]
        except (BotoCoreError, ClientError) as exc:
            raise SecretsUnavailable(f"secrets backend put failed for {name}") from exc

    async def get(self, vault_path: str) -> str:
        try:
            resp = await asyncio.to_thread(self._client.get_secret_value, SecretId=vault_path)
            return resp["SecretString"]
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            # Missing, or scheduled-for-deletion (revoked) → not resolvable.
            if code in ("ResourceNotFoundException", "InvalidRequestException"):
                raise SecretNotFound(f"secret unavailable at the given reference") from exc
            raise SecretsUnavailable("secrets backend get failed") from exc
        except BotoCoreError as exc:
            raise SecretsUnavailable("secrets backend get failed") from exc

    async def rotate(self, vault_path: str, value: str) -> str:
        try:
            await asyncio.to_thread(self._client.put_secret_value, SecretId=vault_path, SecretString=value)
            return vault_path
        except (BotoCoreError, ClientError) as exc:
            raise SecretsUnavailable("secrets backend rotate failed") from exc

    async def disable(self, vault_path: str) -> None:
        try:
            # Scheduled deletion (not force) keeps an audit window; get() then fails → unresolvable.
            await asyncio.to_thread(self._client.delete_secret, SecretId=vault_path,
                                    RecoveryWindowInDays=7)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("ResourceNotFoundException", "InvalidRequestException"):
                return  # already gone / already scheduled → idempotent
            raise SecretsUnavailable("secrets backend disable failed") from exc
        except BotoCoreError as exc:
            raise SecretsUnavailable("secrets backend disable failed") from exc

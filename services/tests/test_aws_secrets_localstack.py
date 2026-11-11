"""Opt-in integration test for the AWS Secrets Manager provider against LocalStack (feature 014, T026).

Skipped by default (like a live-credential test): set `AC_RUN_LOCALSTACK=1` AND have the compose `localstack`
service up (Secrets Manager on :4566) to run it. Proves the `aws` provider's put→get→rotate→disable contract
against a real (emulated) Secrets Manager — the same code path prod uses, only the endpoint differs (FR-019,
SC-006). No real AWS account, no real secret: dummy static creds + a synthetic value.
"""
from __future__ import annotations

import os
import uuid

import pytest

RUN = os.environ.get("AC_RUN_LOCALSTACK") == "1"
pytestmark = pytest.mark.skipif(not RUN, reason="set AC_RUN_LOCALSTACK=1 (and start compose localstack) to run")

ENDPOINT = os.environ.get("AC_AWS_ENDPOINT_URL", "http://localhost:4566")


async def test_aws_provider_put_get_rotate_disable_roundtrip():
    # Dummy static creds so boto3 signs requests; LocalStack ignores their validity.
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
    from api.secrets.aws_provider import AwsProvider
    from api.secrets.base import SecretNotFound

    p = AwsProvider(endpoint_url=ENDPOINT, region="us-east-1", prefix="ac-test", env="test")
    ws = uuid.uuid4().hex
    logical = f"openai-{uuid.uuid4().hex[:8]}"

    arn = await p.put(ws, logical, "sk-localstack-value-1")
    assert arn and await p.get(arn) == "sk-localstack-value-1"

    same = await p.rotate(arn, "sk-localstack-value-2")
    assert await p.get(same) == "sk-localstack-value-2"

    await p.disable(arn)                      # scheduled deletion → no longer resolvable
    with pytest.raises(SecretNotFound):
        await p.get(arn)

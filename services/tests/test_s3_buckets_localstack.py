"""Opt-in integration test for the LocalStack S3 object-store bootstrap (feature 022).

Skipped by default (like the LocalStack secrets test): set `AC_RUN_LOCALSTACK=1` AND have the compose
`localstack` service up with `SERVICES` including `s3` (edge on :4566, init hook created the buckets) to run it.
Proves the dev object-store INFRASTRUCTURE — S3 reachable + the expected buckets bootstrapped — plus a
put→get→delete roundtrip. Same emulator + code path prod uses against real AWS S3; only the endpoint differs.

Uses boto3's S3 client directly (TEST-SIDE only); there is NO app-side storage client yet (feature 022 is
infra-only). No real AWS, no real creds: dummy static creds + a synthetic object.
"""
from __future__ import annotations

import os
import uuid

import pytest

RUN = os.environ.get("AC_RUN_LOCALSTACK") == "1"
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not RUN, reason="set AC_RUN_LOCALSTACK=1 (and start compose localstack with s3) to run"),
]

# S3 shares the LocalStack edge with Secrets Manager — same endpoint env as the secrets test.
ENDPOINT = os.environ.get("AC_AWS_ENDPOINT_URL", "http://localhost:4566")
EXPECTED_BUCKETS = {"ac-uploads", "langfuse"}


def _s3_client():
    # Dummy static creds so boto3 signs requests; LocalStack ignores their validity.
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
    import boto3
    from botocore.config import Config

    # Path-style addressing (LocalStack S3 does not resolve virtual-host buckets on localhost).
    return boto3.client(
        "s3",
        endpoint_url=ENDPOINT,
        region_name="us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def test_bootstrapped_buckets_exist():
    client = _s3_client()
    names = {b["Name"] for b in client.list_buckets().get("Buckets", [])}
    missing = EXPECTED_BUCKETS - names
    assert not missing, f"missing bootstrapped buckets: {missing} (present: {names})"


def test_put_get_delete_roundtrip():
    from botocore.exceptions import ClientError

    client = _s3_client()
    key = f"feature-022/{uuid.uuid4().hex}.txt"
    body = b"agent-catalyst-s3-roundtrip"

    client.put_object(Bucket="ac-uploads", Key=key, Body=body)
    got = client.get_object(Bucket="ac-uploads", Key=key)["Body"].read()
    assert got == body

    client.delete_object(Bucket="ac-uploads", Key=key)
    with pytest.raises(ClientError):  # deleted → no longer resolvable
        client.get_object(Bucket="ac-uploads", Key=key)

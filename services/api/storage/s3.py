"""AWS S3 / LocalStack S3 storage backend (feature 024; S3 not MinIO per feature 022).

Imported lazily by the registry only when `AC_STORAGE_PROVIDER=s3`. Blocking boto3 calls run in a worker thread so they
never stall the event loop (same pattern as api/secrets/aws_provider.py).
"""
from __future__ import annotations

import asyncio

import boto3
from botocore.exceptions import ClientError


class S3Storage:
    def __init__(self, *, bucket: str, endpoint_url: str | None, region: str) -> None:
        self._bucket = bucket
        self._client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    async def put(self, key: str, data: bytes) -> None:
        await asyncio.to_thread(self._client.put_object, Bucket=self._bucket, Key=key, Body=data)

    async def get(self, key: str) -> bytes:
        def _get() -> bytes:
            return self._client.get_object(Bucket=self._bucket, Key=key)["Body"].read()
        return await asyncio.to_thread(_get)

    async def delete(self, key: str) -> None:
        def _delete() -> None:
            try:
                self._client.delete_object(Bucket=self._bucket, Key=key)   # S3 delete is already idempotent
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") not in ("NoSuchKey", "404"):
                    raise
        await asyncio.to_thread(_delete)

    async def list(self, prefix: str):
        """[(key, last_modified)] under `prefix` via the list_objects_v2 paginator (orphan sweep, analyze H5)."""
        def _list():
            out = []
            for page in self._client.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=prefix):
                out.extend((o["Key"], o["LastModified"]) for o in page.get("Contents", []))
            return sorted(out)
        return await asyncio.to_thread(_list)

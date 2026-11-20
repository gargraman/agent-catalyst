#!/bin/bash
# LocalStack S3 bucket bootstrap (feature 022). Runs once LocalStack is READY (mounted into
# /etc/localstack/init/ready.d/). Creates the dev object-store buckets idempotently. `awslocal` ships in the
# LocalStack image and targets the local edge; no real AWS, no real creds.
set -euo pipefail

for bucket in ac-uploads langfuse; do
  if awslocal s3 ls "s3://${bucket}" >/dev/null 2>&1; then
    echo "bucket ${bucket} already exists"
  else
    awslocal s3 mb "s3://${bucket}"
  fi
done

echo "s3 buckets ready"

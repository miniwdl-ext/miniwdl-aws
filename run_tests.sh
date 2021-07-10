#!/bin/bash

set -euo pipefail

cd "$(dirname "$0")"

export AWS_DEFAULT_REGION=$(aws configure get region)
export MINIWDL__AWS__WORKFLOW_IMAGE=$(./build_and_push_image.sh)
if [[ -z $MINIWDL__AWS__WORKFLOW_IMAGE ]]; then
    exit 1
fi
export MINIWDL_AWS_TEST_BUCKET="miniwdl-test-$(aws sts get-caller-identity | jq -r .Account)"
>&2 echo "Creating S3 bucket $MINIWDL_AWS_TEST_BUCKET (BucketAlreadyOwnedByYou error is OK):"
aws s3api create-bucket --bucket "$MINIWDL_AWS_TEST_BUCKET" \
    --region "$AWS_DEFAULT_REGION" --create-bucket-configuration LocationConstraint="$AWS_DEFAULT_REGION" \
    || true

pytest -sxv test/test*.py $@

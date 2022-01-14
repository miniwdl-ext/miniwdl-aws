#!/bin/bash

set -euo pipefail

cd "$(dirname "$0")"

export AWS_DEFAULT_REGION=$(aws configure get region)
if [[ -z ${MINIWDL__AWS__WORKFLOW_IMAGE:-} ]]; then
    export MINIWDL__AWS__WORKFLOW_IMAGE=$(./build_test_image.sh)
    if [[ -z $MINIWDL__AWS__WORKFLOW_IMAGE ]]; then
        exit 1
    fi
fi
export MINIWDL_AWS_TEST_BUCKET="miniwdl-test-$(aws sts get-caller-identity | jq -r .Account)"
>&2 echo "Creating S3 bucket $MINIWDL_AWS_TEST_BUCKET (BucketAlreadyOwnedByYou error is OK):"
aws s3api create-bucket --bucket "$MINIWDL_AWS_TEST_BUCKET" \
    --region "$AWS_DEFAULT_REGION" --create-bucket-configuration LocationConstraint="$AWS_DEFAULT_REGION" \
    || true
# NOTE: workflow IAM role needs to be able to write to that bucket...

pytest -sxv test*.py $@

#!/bin/bash
# Build the miniwdl-aws docker image and push to ECR (of the currently-credentialed account) for
# use with live tests. Prepare in advance:
#     aws ecr create-repository --repository-name miniwdl-aws

set -euo pipefail

# build local image
cd "$(dirname "$0")/.."
>&2 python3 setup.py check
>&2 docker pull public.ecr.aws/amazonlinux/amazonlinux:2023
>&2 docker build -t miniwdl-aws .

# login to ECR
AWS_REGION="$(aws configure get region)"
ECR_REGISTRY_ID="$(aws ecr describe-registry | jq -r .registryId)"
ECR_REPO="${ECR_REGISTRY_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/miniwdl-aws"
aws ecr get-login-password --region $(aws configure get region) \
    | >&2 docker login --username AWS --password-stdin $ECR_REPO

# set ECR tag & push
>&2 docker tag miniwdl-aws:latest ${ECR_REPO}:latest
>&2 docker push ${ECR_REPO}

# print full RepoDigest (for use with `docker pull`) to stdout
>&2 echo
echo "$(docker inspect ${ECR_REPO}:latest | jq -r '.[0].RepoDigests[0]')"

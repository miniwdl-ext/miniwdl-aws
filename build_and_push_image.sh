#!/bin/bash
# Build the miniwdl_aws docker image suitable for submitting to Batch as the "head job."
# Push it to ECR on the currently-credentialed account, prepare in advance:
#     aws ecr create-repository --repository-name miniwdl_aws

set -euo pipefail

# build local image
>&2 docker pull public.ecr.aws/amazonlinux/amazonlinux:2
>&2 docker build -t miniwdl_aws .

# login to ECR
AWS_REGION="$(aws configure get region)"
ECR_REGISTRY_ID="$(aws ecr describe-registry | jq -r .registryId)"
ECR_REPO="${ECR_REGISTRY_ID}.dkr.ecr.${AWS_REGION}.amazonaws.com/miniwdl_aws"
aws ecr get-login-password --region $(aws configure get region) \
    | >&2 docker login --username AWS --password-stdin $ECR_REPO

# set ECR tag & push
>&2 docker tag miniwdl_aws:latest ${ECR_REPO}:latest
>&2 docker push ${ECR_REPO}

# print full RepoDigest (for use with `docker pull`) to stdout
>&2 echo
echo "$(docker inspect ${ECR_REPO}:latest | jq -r '.[0].RepoDigests[0]')"

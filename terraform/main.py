#!/usr/bin/env python
import os
from typing import Optional, List
import boto3
from constructs import Construct
from cdktf import (
    App,
    TerraformStack,
    TerraformOutput,
    TerraformResourceLifecycle,
)
from imports.aws import AwsProvider
from imports.aws.efs import (
    EfsFileSystem,
    EfsMountTarget,
    EfsAccessPoint,
    EfsAccessPointPosixUser,
)
from networking import MiniwdlAwsNetworking
from roles import MiniwdlAwsRoles
from batch import MiniwdlAwsBatch


class MiniwdlAwsStack(TerraformStack):
    def __init__(
        self,
        scope: Construct,
        ns: str,
        aws_region: str,
        max_task_vcpus: int,
        max_workflow_vcpus: int,
        one_zone: Optional[str] = None,
        s3upload_buckets: Optional[List[str]] = None,
        create_spot_service_roles: bool = True,
    ):
        super().__init__(scope, ns)

        AwsProvider(self, "aws", region=aws_region)

        zones = sorted(
            [
                z["ZoneName"]
                for z in boto3.client("ec2", region_name=aws_region).describe_availability_zones(
                    Filters=[{"Name": "state", "Values": ["available"]}]
                )["AvailabilityZones"]
            ]
        )
        if one_zone:
            assert one_zone in zones, f"MINIWDL__AWS__ONE_ZONE should be among: {' '.join(zones)}"
            zones = [one_zone]
        net = MiniwdlAwsNetworking(self, "miniwdl-aws-net", availability_zones=zones)

        efs = EfsFileSystem(
            self,
            "miniwdl-aws-fs",
            encrypted=True,
            availability_zone_name=(zones[0] if len(zones) == 1 else None),
            performance_mode=("maxIO" if len(zones) > 1 else "generalPurpose"),
            lifecycle=TerraformResourceLifecycle(prevent_destroy=True),
        )
        for zone in zones:
            EfsMountTarget(
                self,
                "efs-mount-target-" + zone,
                file_system_id=efs.id,
                subnet_id=net.subnets_by_zone[zone].id,
                security_groups=[net.sg.id],
            )
        efsap = EfsAccessPoint(
            self,
            "miniwdl-aws-fsap",
            file_system_id=efs.id,
            posix_user=EfsAccessPointPosixUser(gid=0, uid=0),
        )

        roles = MiniwdlAwsRoles(
            self, "miniwdl-aws-roles", s3upload_buckets, create_spot_service_roles
        )
        batch = MiniwdlAwsBatch(
            self, "miniwdl-aws-batch", net, roles, efsap, max_task_vcpus, max_workflow_vcpus
        )

        TerraformOutput(self, "fs", value=efs.id)
        TerraformOutput(self, "fsap", value=efsap.id)
        TerraformOutput(self, "taskQueue", value=batch.task_queue.name)
        TerraformOutput(self, "workflowQueue", value=batch.workflow_queue.name)
        TerraformOutput(self, "s3uploadBuckets", value=s3upload_buckets)


print()
aws_region = os.environ.get(
    "AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", boto3.Session().region_name)
)
print(f"AWS region (AWS_REGION) = {aws_region}")
s3upload_buckets = os.environ.get("MINIWDL__AWS__S3UPLOAD_BUCKETS", None)
if s3upload_buckets:
    s3upload_buckets = s3upload_buckets.split(",")
print(
    f"writable S3 buckets (MINIWDL__AWS__S3UPLOAD_BUCKETS) = {', '.join(s3upload_buckets) if s3upload_buckets else '(none)'}"
)
one_zone = os.environ.get("MINIWDL__AWS__ONE_ZONE", None)
if one_zone:
    print(f"AWS one-zone = {one_zone}")
create_spot_service_roles = os.environ.get("SPOT_SERVICE_ROLES", "1").strip().lower() not in (
    "0",
    "false",
    "f",
    "no",
    "n",
)
print(
    f"create AWS spot & spotfleet service roles (SPOT_SERVICE_ROLES) = {'y' if create_spot_service_roles else 'n'}"
)
max_task_vcpus = os.environ.get("MINIWDL__AWS__MAX_TASK_VCPUS", 64)
print(f"max task vCPUs (MINIWDL__AWS__MAX_TASK_VCPUS) = {max_task_vcpus}")
max_workflow_vcpus = os.environ.get("MINIWDL__AWS__MAX_WORKFLOW_VCPUS", 10)
print(f"max workflow engine vCPUs (MINIWDL__AWS__MAX_WORKFLOW_VCPUS) = {max_workflow_vcpus}")
print()

app = App()
MiniwdlAwsStack(
    app,
    "miniwdl-aws-stack",
    aws_region,
    max_task_vcpus,
    max_workflow_vcpus,
    one_zone=one_zone,
    s3upload_buckets=s3upload_buckets,
    create_spot_service_roles=create_spot_service_roles,
)

app.synth()

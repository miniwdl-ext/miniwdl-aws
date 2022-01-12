#!/usr/bin/env python
import os
from typing import Optional
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
        one_zone: Optional[str] = None,
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

        roles = MiniwdlAwsRoles(self, "miniwdl-aws-roles", create_spot_service_roles)
        batch = MiniwdlAwsBatch(self, "miniwdl-aws-batch", net, roles)

        TerraformOutput(self, "fs", value=efs.id)
        TerraformOutput(self, "fsap", value=efsap.id)
        TerraformOutput(self, "taskQueue", value=batch.task_queue.name)
        TerraformOutput(self, "workflowQueue", value=batch.workflow_queue.name)


app = App()
MiniwdlAwsStack(
    app,
    "miniwdl-aws-stack",
    aws_region=os.environ["MINIWDL__AWS__REGION"],
    one_zone=os.environ.get("MINIWDL__AWS__ONE_ZONE", None),
    create_spot_service_roles=(
        os.environ.get("SPOT_SERVICE_ROLES", "1").strip().lower()
        not in ("0", "false", "f", "no", "n")
    ),
)

app.synth()

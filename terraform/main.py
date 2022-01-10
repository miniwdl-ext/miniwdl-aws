#!/usr/bin/env python
import os
from constructs import Construct
from cdktf import App, TerraformStack, TerraformOutput
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
    def __init__(self, scope: Construct, ns: str, availability_zone: str):
        super().__init__(scope, ns)

        AwsProvider(self, "aws", region="us-west-2")
        net = MiniwdlAwsNetworking(
            self, "miniwdl-aws-net", availability_zone=availability_zone
        )

        efs = EfsFileSystem(
            self,
            "miniwdl-aws-fs",
            availability_zone_name=availability_zone,
            encrypted=True,
            # desirable but unsupported for one-zone mode:
            # performance_mode="maxIO",
        )
        EfsMountTarget(
            self,
            "efs-mount-target",
            file_system_id=efs.id,
            subnet_id=net.subnet.id,
            security_groups=[net.sg.id],
        )
        efsap = EfsAccessPoint(
            self,
            "miniwdl-aws-fsap",
            file_system_id=efs.id,
            posix_user=EfsAccessPointPosixUser(gid=0, uid=0),
        )

        roles = MiniwdlAwsRoles(self, "miniwdl-aws-roles")
        batch = MiniwdlAwsBatch(self, "miniwdl-aws-batch", net, roles)

        TerraformOutput(self, "fs", value=efs.id)
        TerraformOutput(self, "fsap", value=efsap.id)
        TerraformOutput(self, "taskQueue", value=batch.task_queue.name)
        TerraformOutput(self, "workflowQueue", value=batch.workflow_queue.name)


app = App()
MiniwdlAwsStack(
    app,
    "miniwdl-aws-stack",
    availability_zone=os.environ["MINIWDL__AWS__AVAILABILITY_ZONE"],
)

app.synth()

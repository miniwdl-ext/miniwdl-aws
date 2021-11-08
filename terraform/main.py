#!/usr/bin/env python
import os
from constructs import Construct
from cdktf import App, TerraformStack
from imports.aws import AwsProvider
from imports.aws.efs import EfsFileSystem, EfsMountTarget, EfsAccessPoint, EfsAccessPointPosixUser
from networking import MiniwdlAwsNetworking


class MiniwdlAwsStack(TerraformStack):
    def __init__(self, scope: Construct, ns: str, availability_zone: str):
        super().__init__(scope, ns)

        AwsProvider(self, "aws", region="us-west-2")
        net = MiniwdlAwsNetworking(self, "miniwdl-aws-net", availability_zone=availability_zone)
        efs = EfsFileSystem(
            self,
            "miniwdl-aws-fs",
            availability_zone_name=availability_zone,
            encrypted=True,
            performance_mode="maxIO",
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


app = App()
MiniwdlAwsStack(
    app, "miniwdl-aws-stack", availability_zone=os.environ["MINIWDL__AWS__AVAILABILITY_ZONE"]
)

app.synth()

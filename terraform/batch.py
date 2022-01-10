from base64 import b64encode
from constructs import Construct
from imports.aws.batch import (
    BatchComputeEnvironment,
    BatchComputeEnvironmentComputeResources,
    BatchComputeEnvironmentComputeResourcesLaunchTemplate,
    BatchJobQueue,
)
from imports.aws.ec2 import LaunchTemplate, LaunchTemplateIamInstanceProfile
from imports.aws.iam import IamInstanceProfile
from networking import MiniwdlAwsNetworking
from roles import MiniwdlAwsRoles


class MiniwdlAwsBatch(Construct):
    task_queue: BatchJobQueue
    workflow_queue: BatchJobQueue

    def __init__(
        self,
        scope: Construct,
        ns: str,
        net: MiniwdlAwsNetworking,
        roles: MiniwdlAwsRoles,
    ):
        super().__init__(scope, ns)

        task_instance_profile = IamInstanceProfile(
            self, "task-instance-profile", role=roles.task_role.name
        )

        task_launch_template = LaunchTemplate(
            self,
            "task-launch-template",
            iam_instance_profile=LaunchTemplateIamInstanceProfile(arn=task_instance_profile.arn),
            user_data=_task_instance_user_data,
        )

        task_ce = BatchComputeEnvironment(
            self,
            "task-compute-env",
            compute_environment_name="miniwdl-task-compute",
            type="MANAGED",
            compute_resources=BatchComputeEnvironmentComputeResources(
                subnets=[net.subnet.id],
                security_group_ids=[net.sg.id],
                max_vcpus=64,  # TODO: make configurable
                instance_type=["m5d", "c5d", "r5d"],
                type="SPOT",
                allocation_strategy="SPOT_CAPACITY_OPTIMIZED",
                spot_iam_fleet_role=roles.spot_fleet_role.arn,
                instance_role=task_instance_profile.arn,
                launch_template=BatchComputeEnvironmentComputeResourcesLaunchTemplate(
                    launch_template_id=task_launch_template.id
                ),
            ),
            service_role=roles.batch_role.arn,
        )

        self.task_queue = BatchJobQueue(
            self,
            "task-queue",
            compute_environments=[task_ce.arn],
            name="wdl-tasks",
            priority=1,
            state="ENABLED",
        )

        workflow_ce = BatchComputeEnvironment(
            self,
            "workflow-compute-env",
            compute_environment_name="miniwdl-workflow-compute",
            type="MANAGED",
            compute_resources=BatchComputeEnvironmentComputeResources(
                type="FARGATE",
                max_vcpus=10,  # TODO: make configurable
                subnets=[net.subnet.id],
                security_group_ids=[net.sg.id],
            ),
            service_role=roles.batch_role.arn,
        )

        self.workflow_queue = BatchJobQueue(
            self,
            "workflow-queue",
            compute_environments=[workflow_ce.arn],
            name="wdl-workflows",
            priority=1,
            state="ENABLED",
            tags={
                "WorkflowEngineRoleArn": roles.workflow_role.arn,
                "DefaultTaskQueue": self.task_queue.name,
            },
        )


_task_instance_user_data = b64encode(
    """
#!/bin/bash
# To run on first boot of an EC2 instance with NVMe instance storage volumes:
# 1) Assembles them into a RAID0 array, formats with XFS, and mounts to /mnt/scratch
# 2) Replaces /var/lib/docker with a symlink to /mnt/scratch/docker so that docker images and
#    container file systems use this high-performance scratch space. (restarts docker)
# The configuration persists through reboots (but not instance stop).
# logs go to /var/log/cloud-init-output.log
# refs:
# https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ssd-instance-store.html
# https://github.com/kislyuk/aegea/blob/master/aegea/rootfs.skel/usr/bin/aegea-format-ephemeral-storage

set -euxo pipefail
shopt -s nullglob


devices=(/dev/xvd[b-m] /dev/disk/by-id/nvme-Amazon_EC2_NVMe_Instance_Storage_AWS?????????????????)
num_devices="${#devices[@]}"
if (( num_devices > 0 )) && ! grep /dev/md0 <(df); then
    mdadm --create /dev/md0 --force --auto=yes --level=0 --chunk=256 --raid-devices=${num_devices} ${devices[@]}
    mkfs.xfs -f /dev/md0
    mkdir -p /mnt/scratch
    mount -o defaults,noatime,largeio,logbsize=256k -t xfs /dev/md0 /mnt/scratch
    echo UUID=$(blkid -s UUID -o value /dev/md0) /mnt/scratch xfs defaults,noatime,largeio,logbsize=256k 0 2 >> /etc/fstab
    update-initramfs -u
fi
mkdir -p /mnt/scratch/tmp


systemctl stop docker || true
if [ -d /var/lib/docker ] && [ ! -L /var/lib/docker ]; then
    mv /var/lib/docker /mnt/scratch
fi
mkdir -p /mnt/scratch/docker
ln -s /mnt/scratch/docker /var/lib/docker
systemctl restart docker || true
""".lstrip().encode()
).decode("ascii")

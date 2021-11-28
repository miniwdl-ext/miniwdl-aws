from constructs import Construct
from imports.aws.iam import IamRole, IamServiceLinkedRole


class MiniwdlAwsRoles(Construct):
    task_role: IamRole
    workflow_role: IamRole
    spot_fleet_role: IamRole
    batch_role: IamRole

    def __init__(self, scope: Construct, ns: str):
        super().__init__(scope, ns)

        self.task_role = IamRole(
            self,
            "task-role",
            assume_role_policy="""
                {
                    "Version": "2012-10-17",
                    "Statement": [
                    {
                        "Action": "sts:AssumeRole",
                        "Principal": {
                        "Service": "ec2.amazonaws.com"
                        },
                        "Effect": "Allow",
                        "Sid": ""
                    }
                    ]
                }
            """,
            managed_policy_arns=[
                "arn:aws:iam::aws:policy/" + p
                for p in (
                    "service-role/AmazonEC2ContainerServiceforEC2Role",
                    "AmazonElasticFileSystemClientReadWriteAccess",
                    "AmazonEC2ContainerRegistryReadOnly",
                    "AmazonS3ReadOnlyAccess",
                )
            ],
        )

        self.workflow_role = IamRole(
            self,
            "workflow-role",
            assume_role_policy="""
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                        "Sid": "",
                        "Effect": "Allow",
                        "Principal": {
                            "Service": "ecs-tasks.amazonaws.com"
                        },
                        "Action": "sts:AssumeRole"
                        }
                    ]
                }
            """,
            managed_policy_arns=[
                "arn:aws:iam::aws:policy/" + p
                for p in (
                    "service-role/AmazonECSTaskExecutionRolePolicy",
                    "AWSBatchFullAccess",
                    "AmazonElasticFileSystemClientFullAccess",
                    "AmazonEC2ContainerRegistryPowerUser",
                    "AmazonS3ReadOnlyAccess",
                )
            ],
        )
        # TODO: inline policy for --s3upload bucket

        self.batch_role = IamRole(
            self,
            "batch-role",
            assume_role_policy="""
                {
                    "Version": "2012-10-17",
                    "Statement": [
                    {
                        "Action": "sts:AssumeRole",
                        "Effect": "Allow",
                        "Principal": {
                        "Service": "batch.amazonaws.com"
                        }
                    }
                    ]
                },
            """,
            managed_policy_arns=["arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"],
        )
        self.spot_fleet_role = IamRole(
            self,
            "spot-fleet-role",
            assume_role_policy="""
                {"Version":"2012-10-17","Statement":[{"Sid":"","Effect":"Allow","Principal":{"Service":"spotfleet.amazonaws.com"},"Action":"sts:AssumeRole"}]}
            """,
            managed_policy_arns=[
                "arn:aws:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole"
            ],
        )

        IamServiceLinkedRole(self, "spot-service-role", aws_service_name="spot.amazonaws.com")
        IamServiceLinkedRole(
            self, "spot-fleet-service-role", aws_service_name="spotfleet.amazonaws.com"
        )

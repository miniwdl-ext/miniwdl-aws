import json
from typing import List, Optional
from constructs import Construct
from imports.aws.iam import IamRole, IamRoleInlinePolicy, IamServiceLinkedRole


class MiniwdlAwsRoles(Construct):
    task_role: IamRole
    workflow_role: IamRole
    spot_fleet_role: IamRole
    batch_role: IamRole

    def __init__(
        self,
        scope: Construct,
        ns: str,
        s3upload_buckets: Optional[List[str]] = None,
        create_spot_service_roles: bool = True,
    ):
        super().__init__(scope, ns)

        self.task_role = IamRole(
            self,
            "task-role",
            name_prefix="wdl-tasks-",
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
            """.strip(),
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

        s3upload_inline_policy = None
        if s3upload_buckets:
            s3upload_bucket_arns = ["arn:aws:s3:::" + b for b in s3upload_buckets]
            s3upload_resource_arns = [arn + "/*" for arn in s3upload_bucket_arns]
            s3upload_inline_policy = IamRoleInlinePolicy(
                name="s3upload-policy",
                policy=json.dumps(
                    {
                        "Version": "2012-10-17",
                        "Statement": [
                            {
                                "Effect": "Allow",
                                "Action": ["s3:ListBucket"],
                                "Resource": s3upload_bucket_arns,
                            },
                            {
                                "Effect": "Allow",
                                "Action": ["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                                "Resource": s3upload_resource_arns,
                            },
                        ],
                    }
                ),
            )

        workflow_managed_policies = [
            "service-role/AmazonECSTaskExecutionRolePolicy",
            "AWSBatchFullAccess",
            "AmazonElasticFileSystemClientFullAccess",
            "AmazonEC2ContainerRegistryReadOnly",
            "AmazonS3ReadOnlyAccess",
        ]

        self.workflow_role = IamRole(
            self,
            "workflow-role",
            name_prefix="wdl-workflows-",
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
            """.strip(),
            managed_policy_arns=["arn:aws:iam::aws:policy/" + p for p in workflow_managed_policies],
            inline_policy=[s3upload_inline_policy] if s3upload_inline_policy else None,
        )

        self.batch_role = IamRole(
            self,
            "batch-role",
            name_prefix="wdl-batch-",
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
                }
            """.strip(),
            managed_policy_arns=["arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"],
        )
        self.spot_fleet_role = IamRole(
            self,
            "spot-fleet-role",
            name_prefix="wdl-spot-",
            assume_role_policy="""
                {"Version":"2012-10-17","Statement":[{"Sid":"","Effect":"Allow","Principal":{"Service":"spotfleet.amazonaws.com"},"Action":"sts:AssumeRole"}]}
            """.strip(),
            managed_policy_arns=[
                "arn:aws:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole"
            ],
        )

        # These service-linked roles can be created only once per account, so we make them optional
        # cf. https://github.com/cloudposse/terraform-aws-elasticsearch/issues/5
        if create_spot_service_roles:
            IamServiceLinkedRole(self, "spot-service-role", aws_service_name="spot.amazonaws.com")
            IamServiceLinkedRole(
                self,
                "spot-fleet-service-role",
                aws_service_name="spotfleet.amazonaws.com",
            )

provider "aws" {
  default_tags {
    tags = {
      Owner       = var.owner_tag
      Environment = var.environment_tag
    }
  }
}

data "aws_availability_zones" "available" {}

/**************************************************************************************************
 * Networking
 *************************************************************************************************/

resource "aws_vpc" "vpc" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
}

resource "aws_subnet" "public" {
  count                   = length(data.aws_availability_zones.available.names)
  vpc_id                  = aws_vpc.vpc.id
  cidr_block              = "10.0.${32 * count.index}.0/20"
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true
}

resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.vpc.id
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.vpc.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.igw.id
  }
}

resource "aws_route_table_association" "public" {
  count          = length(aws_subnet.public)
  route_table_id = aws_route_table.public.id
  subnet_id      = aws_subnet.public[count.index].id
}

resource "aws_security_group" "all" {
  name   = var.environment_tag
  vpc_id = aws_vpc.vpc.id
  ingress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = [aws_vpc.vpc.cidr_block]
  }
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

/**************************************************************************************************
 * EFS
 *************************************************************************************************/

resource "aws_efs_file_system" "efs" {
  encrypted        = true
  performance_mode = "maxIO"
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_efs_mount_target" "target" {
  count           = length(aws_subnet.public)
  file_system_id  = aws_efs_file_system.efs.id
  subnet_id       = aws_subnet.public[count.index].id
  security_groups = [aws_security_group.all.id]
}

resource "aws_efs_access_point" "ap" {
  file_system_id = aws_efs_file_system.efs.id
  posix_user {
    uid = 0
    gid = 0
  }
}

/**************************************************************************************************
 * Batch
 *************************************************************************************************/

resource "aws_iam_instance_profile" "task" {
  name = "${var.environment_tag}-task"
  role = aws_iam_role.task.name
}

resource "aws_launch_template" "task" {
  name = "${var.environment_tag}-task"
  iam_instance_profile {
    name = aws_iam_instance_profile.task.name
  }
  user_data = filebase64("${path.module}/task_instance_user_data")
}

resource "aws_batch_compute_environment" "task" {
  compute_environment_name = "${var.environment_tag}-task"
  type                     = "MANAGED"
  service_role             = aws_iam_role.batch.arn

  compute_resources {
    type                = "SPOT"
    instance_type       = ["m5d", "c5d", "r5d"]
    allocation_strategy = "SPOT_CAPACITY_OPTIMIZED"
    max_vcpus           = var.task_max_vcpus
    subnets             = aws_subnet.public[*].id
    security_group_ids  = [aws_security_group.all.id]
    spot_iam_fleet_role = aws_iam_role.spot_fleet.arn
    instance_role       = aws_iam_instance_profile.task.arn

    launch_template {
      launch_template_id = aws_launch_template.task.id
    }
  }
}

resource "aws_batch_job_queue" "task" {
  name                 = "${var.environment_tag}-task"
  state                = "ENABLED"
  priority             = 1
  compute_environments = [aws_batch_compute_environment.task.arn]
}

resource "aws_batch_compute_environment" "workflow" {
  compute_environment_name = "${var.environment_tag}-workflow"
  type                     = "MANAGED"
  service_role             = aws_iam_role.batch.arn

  compute_resources {
    type               = "FARGATE"
    max_vcpus          = var.workflow_max_vcpus
    subnets            = aws_subnet.public[*].id
    security_group_ids = [aws_security_group.all.id]
  }
}

resource "aws_batch_job_queue" "workflow" {
  name                 = "${var.environment_tag}-workflow"
  state                = "ENABLED"
  priority             = 1
  compute_environments = [aws_batch_compute_environment.workflow.arn]

  # miniwdl-aws-submit detects defaults from these tags
  tags = {
    WorkflowEngineRoleArn = aws_iam_role.workflow.arn
    DefaultTaskQueue      = aws_batch_job_queue.task.name
    DefaultFsap           = aws_efs_access_point.ap.id
  }
}

/**************************************************************************************************
 * IAM roles
 *************************************************************************************************/

# for Batch EC2 worker instances running WDL tasks
resource "aws_iam_role" "task" {
  name = "${var.environment_tag}-task"

  assume_role_policy = <<-EOF
  {"Statement":[{"Principal":{"Service":"ec2.amazonaws.com"},"Sid":"","Effect":"Allow","Action":"sts:AssumeRole"}],"Version":"2012-10-17"}
  EOF

  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role",
    "arn:aws:iam::aws:policy/AmazonElasticFileSystemClientReadWriteAccess",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
  ]
}

# for Batch Fargate tasks running miniwdl itself
resource "aws_iam_role" "workflow" {
  name = "${var.environment_tag}-workflow"

  assume_role_policy = <<-EOF
  {"Statement":[{"Principal":{"Service":"ecs-tasks.amazonaws.com"},"Sid":"","Effect":"Allow","Action":"sts:AssumeRole"}],"Version":"2012-10-17"}
  EOF

  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy",
    "arn:aws:iam::aws:policy/AWSBatchFullAccess",
    "arn:aws:iam::aws:policy/AmazonElasticFileSystemClientFullAccess",
    "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly",
    "arn:aws:iam::aws:policy/AmazonS3ReadOnlyAccess",
  ]

  # permissions for --s3upload
  dynamic "inline_policy" {
    for_each = length(var.s3upload_buckets) > 0 ? [true] : []

    content {
      name = "${var.environment_tag}-workflow-s3upload"
      policy = jsonencode({
        Version = "2012-10-17",
        Statement = [
          {
            Effect   = "Allow",
            Action   = ["s3:ListBucket"],
            Resource = formatlist("arn:aws:s3:::%s", var.s3upload_buckets),
          },
          {
            Effect   = "Allow",
            Action   = ["s3:GetObject", "s3:PutObject"],
            Resource = formatlist("arn:aws:s3:::%s/*", var.s3upload_buckets),
          },
        ],
      })
    }
  }
}

# Boilerplate roles

resource "aws_iam_role" "batch" {
  name = "${var.environment_tag}-batch"

  assume_role_policy = <<-EOF
  {"Statement":[{"Principal":{"Service":"batch.amazonaws.com"},"Sid":"","Effect":"Allow","Action":"sts:AssumeRole"}],"Version":"2012-10-17"}
  EOF

  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole",
  ]
}

resource "aws_iam_role" "spot_fleet" {
  name = "${var.environment_tag}-spot"

  assume_role_policy = <<-EOF
  {"Statement":[{"Principal":{"Service":"spotfleet.amazonaws.com"},"Sid":"","Effect":"Allow","Action":"sts:AssumeRole"}],"Version":"2012-10-17"}
  EOF

  managed_policy_arns = [
    "arn:aws:iam::aws:policy/service-role/AmazonEC2SpotFleetTaggingRole",
  ]
}

# The following service-linked roles can be created only once per account; trying to create them
# again fails the deploy, in which case set variable create_spot_service_roles = false.
# info: https://github.com/cloudposse/terraform-aws-elasticsearch/issues/5

resource "aws_iam_service_linked_role" "spot" {
  count            = var.create_spot_service_roles ? 1 : 0
  aws_service_name = "spot.amazonaws.com"
}

resource "aws_iam_service_linked_role" "spotfleet" {
  count            = var.create_spot_service_roles ? 1 : 0
  aws_service_name = "spotfleet.amazonaws.com"
}

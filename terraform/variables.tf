variable "owner_tag" {
  description = "Owner tag applied to all resources, e.g. your username/email"
}

variable "environment_tag" {
  description = "Environment tag applied to all resources, and used in some resource names"
  default     = "miniwdl"
}

variable "s3upload_buckets" {
  description = "S3 bucket name(s) for automatic upload of workflow outputs with `miniwdl-aws-submit --s3upload`"
  type        = list(string)
  default     = []
}

variable "create_spot_service_roles" {
  description = "Create account-wide spot service roles (disable if they already exist)"
  type        = bool
  default     = true
}

variable "task_max_vcpus" {
  description = "Maximum vCPUs for task compute environment"
  type        = number
  default     = 256
}

variable "workflow_max_vcpus" {
  description = "Maximum vCPUs for workflow compute environment"
  type        = number
  default     = 16
}

output "workflow_queue" {
    value = aws_batch_job_queue.workflow.name
    description = "Batch job queue for workflow jobs (tagged so that miniwdl-aws-submit can detect workflow role, task job queue, and EFS access point)"
}

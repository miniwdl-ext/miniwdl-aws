# miniwdl AWS plugin

**Extends [miniwdl](https://github.com/chanzuckerberg/miniwdl) to run workflows on [AWS Batch](https://aws.amazon.com/batch/) and [EFS](https://aws.amazon.com/efs/)**

This miniwdl plugin enables it to submit AWS Batch jobs to execute WDL tasks. It uses EFS for work-in-progress file I/O, with optional S3 rails for workflow-level I/O.

## Use within Amazon SageMaker Studio

Start with the companion [**miniwdl-aws-studio**](https://github.com/miniwdl-ext/miniwdl-aws-studio) recipe to install miniwdl for interactive use within [Amazon SageMaker Studio](https://aws.amazon.com/sagemaker/studio/), a web IDE with a terminal and filesystem browser. You can use the terminal to operate `miniwdl run` against AWS Batch, the filesystem browser to manage the inputs and outputs on EFS, and the Jupyter notebooks to further analyze the outputs.

That's the best way to try miniwdl-aws and get familiar with how it works with Batch and EFS. Read on for non-interactive deployment, which is a bit more complicated.

## Unattended operations

For non-interactive use, a command-line wrapper `miniwdl-aws-submit` *launches miniwdl in its own small Batch job* to orchestrate the workflow. This **workflow job** then spawns **task jobs** as needed, without needing the submitting computer (e.g. your laptop) to remain connected for the duration. Separate Batch compute environments handle workflow & task jobs, using lightweight [Fargate](https://docs.aws.amazon.com/batch/latest/userguide/fargate.html) resources for workflow jobs. (See below for detailed infra specs.)

### Submitting workflow jobs

First `pip3 install miniwdl-aws` locally to make the `miniwdl-aws-submit` program available. The following example launches a [viral genome assembly](https://github.com/broadinstitute/viral-pipelines/) that should run in 10-15 minutes:

```
miniwdl-aws-submit \
  https://github.com/broadinstitute/viral-pipelines/raw/v2.1.28.0/pipes/WDL/workflows/assemble_refbased.wdl \
  reads_unmapped_bams=https://github.com/broadinstitute/viral-pipelines/raw/v2.1.19.0/test/input/G5012.3.testreads.bam \
  reference_fasta=https://github.com/broadinstitute/viral-pipelines/raw/v2.1.19.0/test/input/ebov-makona.fasta \
  sample_name=G5012.3 \
  --workflow-queue miniwdl_workflow \
  --task-queue miniwdl_task \
  --fsap fsap-xxxx \
  --s3upload s3://MY_BUCKET/assemble_refbased_test \
  --follow
```

The command line resembles `miniwdl run`'s with extra AWS-related arguments:

|command-line argument|equivalent environment variable| |
|---------------------|-------------------------------|-|
| `--workflow-queue`  | `MINIWDL__AWS__WORKFLOW_QUEUE`| Batch job queue on which to schedule the *workflow* job |
| `--task-queue` | `MINIWDL__AWS__TASK_QUEUE` | Batch job queue on which to schedule *task* jobs |
| `--fsap` | `MINIWDL__AWS__FSAP` | [EFS Access Point](https://docs.aws.amazon.com/efs/latest/ug/efs-access-points.html) ID, which workflow and task jobs will mount at `/mnt/efs` |
| `--s3upload` | | (optional) S3 URI prefix under which to upload the workflow products, including the log and output files |

Adding `--wait` makes the tool await the workflow job's success or failure, reproducing miniwdl's exit code. `--follow` does the same and also live-streams the workflow log. Without `--wait` or `--follow`, the tool displays the workflow job UUID and exits immediately.

Arguments not consumed by `miniwdl-aws-submit` are *passed through* to `miniwdl run` inside the workflow job; as are environment variables whose names begin with `MINIWDL__`, allowing override of any [miniwdl configuration option](https://miniwdl.readthedocs.io/en/latest/runner_reference.html#configuration) (disable wih `--no-env`). See [miniwdl_aws.cfg](miniwdl_aws.cfg) for various options preconfigured in the workflow job container.

## Run directories on EFS

Miniwdl runs the workflow in a directory beneath `/mnt/efs/miniwdl_run` (override with `--dir`). The outputs also remain cached there for potential reuse in future runs.

Given the EFS-centric I/O model, you'll need a way to manage the filesystem contents remotely. Deploy an instance or container mounting your EFS, to access via SSH or web app (e.g. [JupyterHub](https://jupyter.org/hub), [Cloud Commander](http://cloudcmd.io/), [VS Code server](https://github.com/cdr/code-server)).

You can also automate cleanup of EFS run directories by setting `miniwdl-aws-submit --s3upload` and:

* `--delete-after success` to delete the run directory immediately after successful output upload
* `--delete-after failure` to delete the directory after failure
* `--delete-after always` to delete it in either case
* (or set environment variable `MINIWDL__AWS__DELETE_AFTER_S3_UPLOAD`)

Deleting a run directory after success prevents the outputs from being reused in future runs. Deleting it after failures can make debugging more difficult (although logs are retained, see below).

## Logs & troubleshooting

If the terminal log isn't available (through Studio or `miniwdl_submit_awsbatch --follow`) to trace a workflow failure, look for miniwdl's usual log files written in the run directory on EFS.

Each task job's log is also forwarded to [CloudWatch Logs](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/WhatIsCloudWatchLogs.html) under the `/aws/batch/job` group and a log stream name reported in miniwdl's log. Using `miniwdl_submit_awsbatch`, the workflow job's log is also forwarded. CloudWatch Logs indexes the logs for structured search through the AWS Console & API.

Misconfigured infrastructure might prevent logs from being written to EFS or CloudWatch at all. In that case, use the AWS Batch console/API to find status messages for the workflow or task jobs.

## Appendix: expected AWS infrastructure

Requirements:

1. VPC in desired region
2. EFS + Access Point providing uid=0 access
    * set `--fsap` or `MINIWDL__AWS__FSAP` to access point ID (fsap-xxxx)
3. Task execution:
    * Batch Compute Environment
        * spot instances (including `AmazonEC2SpotFleetTaggingRole`)
        * instance role
            * `service-role/AmazonEC2ContainerServiceforEC2Role`
            * `AmazonElasticFileSystemClientReadWriteAccess`
            * `AmazonEC2ContainerRegistryReadOnly`
            * `s3:Get*`, `s3:List*` on any desired S3 buckets
            * `s3:Put*` on S3 bucket(s) for `--s3upload`
    * Batch Job Queue connected to compute environment
        * set `--task-queue` or `MINIWDL__AWS__TASK_QUEUE` to queue name
4. Unattended workflow execution with `miniwdl_submit_awsbatch`:
    * Batch Compute Environment
        * Fargate resources
        * execution role
            * `service-role/AmazonECSTaskExecutionRolePolicy`
            * `AWSBatchFullAccess`
            * `AmazonElasticFileSystemFullAccess`
            * `AmazonEC2ContainerRegistryPowerUser`
            * `s3:Get*`, `s3:List*` on any desired S3 buckets
            * `s3:Put*` on S3 bucket(s) for `--s3upload`
    * Batch Job Queue connected to compute environment 
        * tag the queue with `WorkflowEngineRoleArn` set to the execution role's ARN
        * set `--workflow-queue` or `MINIWDL__AWS__WORKFLOW_QUEUE` to queue name

Recommendations:

* Compute environments
    * Modify spot instance launch template to relocate docker operations onto NVMe or an [autoscaling volume](https://github.com/awslabs/amazon-ebs-autoscale) instead of the root EBS volume
    * Set task environment allocation strategy to `SPOT_CAPACITY_OPTIMIZED` and spot bid policies as needed
    * Set task environment spot `bidPercentage` as needed
    * Temporarily set task environment `minvCpus` to reduce scheduling delays during periods of interactive use
* EFS
    * Create in One Zone mode, and restrict compute environments to the same zone (trading off availability for cost)
    * Create in Max I/O mode
    * Configure EFS Access Point to use a nonzero user ID, with an owned filesystem root directory
    * Temporarily provision throughput if starting an intensive workload without much data already stored
* Use non-default VPC security group for EFS & compute environments
    * EFS must be accessible to all containers through TCP port 2049

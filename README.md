# miniwdl AWS plugin

**Extends [miniwdl](https://github.com/chanzuckerberg/miniwdl) to run workflows on [AWS Batch](https://aws.amazon.com/batch/) and [EFS](https://aws.amazon.com/efs/)**

This miniwdl plugin enables it to execute WDL tasks as AWS Batch jobs. It uses EFS for work-in-progress file I/O, optionally uploading final workflow outputs to S3. There are a few ways to use it:

## Use with Amazon Genomics CLI

[<img width="985" src="https://d1.awsstatic.com/product-page-diagram_Amazon-Genomics-CLI_HIW-V3%402x.ccc89cf68c3249249ae34f2ab77872d73565a236.png">](https://aws.amazon.com/genomics-cli/)

[Amazon Genomics CLI](https://aws.amazon.com/genomics-cli/) can deploy a [miniwdl context](https://aws.github.io/amazon-genomics-cli/docs/workflow-engines/miniwdl/) with everything pre-configured.

## Use with Amazon SageMaker Studio

Or, try the [**miniwdl-aws-studio**](https://github.com/miniwdl-ext/miniwdl-aws-studio) recipe to install miniwdl for interactive use within [Amazon SageMaker Studio](https://aws.amazon.com/sagemaker/studio/), a web IDE with a terminal and filesystem browser. You can use the terminal to operate `miniwdl run` against AWS Batch, the filesystem browser to manage the inputs and outputs on EFS, and the Jupyter notebooks to further analyze the outputs.

[<img width="985" alt="image" src="https://user-images.githubusercontent.com/356550/135670945-62ccb938-4195-4537-8eaf-9a0d57f97ea1.png">](https://github.com/miniwdl-ext/miniwdl-aws-studio)

## `miniwdl-aws-submit` with custom infrastructure

Lastly, advanced operators can use [**miniwdl-aws-terraform**](https://github.com/miniwdl-ext/miniwdl-aws-terraform) to deploy/customize the necessary AWS infrastructure, including a VPC, EFS file system, Batch queues, and IAM roles.

In this scheme, a local command-line wrapper `miniwdl-aws-submit` *launches miniwdl in its own small Batch job* to orchestrate a workflow. This **workflow job** then spawns WDL **task jobs** as needed, without needing the submitting laptop to remain connected for the duration. The workflow jobs run on lightweight [Fargate](https://docs.aws.amazon.com/batch/latest/userguide/fargate.html) resources, while task jobs run on EC2 spot instances.

### Submitting workflow jobs

After deploying [miniwdl-aws-terraform](https://github.com/miniwdl-ext/miniwdl-aws-terraform), `pip3 install miniwdl-aws` locally to make the `miniwdl-aws-submit` program available. Try the self-test:

```
miniwdl-aws-submit --self-test --follow --workflow-queue miniwdl-workflow 
```

Then launch a [viral genome assembly](https://github.com/broadinstitute/viral-pipelines/) that should run in 10-15 minutes:

```
miniwdl-aws-submit \
  https://github.com/broadinstitute/viral-pipelines/raw/v2.1.28.0/pipes/WDL/workflows/assemble_refbased.wdl \
  reads_unmapped_bams=https://github.com/broadinstitute/viral-pipelines/raw/v2.1.19.0/test/input/G5012.3.testreads.bam \
  reference_fasta=https://github.com/broadinstitute/viral-pipelines/raw/v2.1.19.0/test/input/ebov-makona.fasta \
  sample_name=G5012.3 \
  --workflow-queue miniwdl-workflow \
  --s3upload s3://MY-BUCKET/assemblies \
  --verbose --follow
```

The command line resembles `miniwdl run`'s with extra AWS-related arguments:

* `--workflow-queue` Batch job queue on which to schedule the workflow job; output from miniwdl-aws-terraform, default `miniwdl-workflow`. (Also set by environment variable `MINIWDL__AWS__WORKFLOW_QUEUE`)
* `--follow` live-streams the workflow log instead of exiting immediately upon submission. (`--wait` blocks on the workflow without streaming the log.)
* `--s3upload` (optional) S3 folder URI under which to upload the workflow products, including the log and output files (if successful). The bucket must be allow-listed in the miniwdl-aws-terraform deployment.
  * Unless `--s3upload` ends with /, one more subfolder is added to the uploaded URI prefix, equal to miniwdl's automatic timestamp-prefixed run name. If it does end in /, then the uploads go directly into/under that folder (and a repeat invocation would be expected to overwrite them).

`miniwdl-aws-submit` detects other infrastructure details (task queue, EFS access point, IAM role) based on the workflow queue; see `miniwdl-aws-submit --help` for additional options to override those defaults.

If the specified WDL source code is an existing local .wdl or .zip file, `miniwdl-aws-submit` automatically ships it in the workflow job definition as the WDL to execute. Given a .wdl file, it runs `miniwdl zip` to detect & include any imported WDL files. Use `miniwdl zip --input file.json` beforehand to also include a JSON file with default workflow inputs (an alternative to writing them out on the command line).

Arguments not consumed by `miniwdl-aws-submit` are *passed through* to `miniwdl run` inside the workflow job; as are environment variables whose names begin with `MINIWDL__`, allowing override of any [miniwdl configuration option](https://miniwdl.readthedocs.io/en/latest/runner_reference.html#configuration) (disable wih `--no-env`). See [miniwdl_aws.cfg](miniwdl_aws.cfg) for various options preconfigured in the workflow job container.

The workflow and task jobs all mount EFS at `/mnt/efs`. Although workflow input files are usually specified using HTTPS or S3 URIs, files already resident on EFS can be used with their `/mnt/efs` paths (which probably don't exist locally on the submitting machine). Unlike the WDL source code, `miniwdl-aws-submit` will not attempt to ship/upload local input files.

## Run directories on EFS

Miniwdl runs the workflow in a directory beneath `/mnt/efs/miniwdl_run` (override with `--dir`). The outputs also remain cached there for potential reuse in future runs (to avoid, submit with `--no-cache` or wipe `/mnt/efs/miniwdl_run/_CACHE`).

Given the EFS-centric I/O model, you'll need a way to browse and manage the filesystem contents remotely. The companion recipe [lambdash-efs](https://github.com/miniwdl-ext/lambdash-efs) is one option; miniwdl-aws-terraform outputs the infrastructure details needed to deploy it (pick any subnet). Or, set up an instance/container mounting your EFS, to access via SSH or web app (e.g. [JupyterHub](https://jupyter.org/hub), [Cloud Commander](http://cloudcmd.io/), [VS Code server](https://github.com/cdr/code-server)).

You can also automate cleanup of EFS run directories by setting `miniwdl-aws-submit --s3upload` and:

* `--delete-after success` to delete the run directory immediately after successful output upload
* `--delete-after failure` to delete the directory after failure
* `--delete-after always` to delete it in either case
* (or set environment variable `MINIWDL__AWS__DELETE_AFTER_S3_UPLOAD`)

Deleting a run directory after success prevents the outputs from being reused in future runs. Deleting it after failures can make debugging more difficult (although logs are retained, see below).

### Security note on file system isolation

Going through AWS Batch & EFS, miniwdl can't enforce the strict file system isolation between WDL task containers that it does locally. All the AWS Batch containers have read/write access to the entire EFS file system (as viewed through the access point), not only their initial working directory.

This is usually benign, because WDL tasks should only read their declared inputs and write into their respective working/temporary directories. But poorly- or maliciously-written tasks could read & write files elsewhere on EFS, even changing their own input files or those of other tasks. This risks unintentional side-effects or worse security hazards from untrusted code.

To mitigate this, test workflows thoroughly using the local backend, which strictly isolates task containers' file systems. If WDL tasks insist on modifying their input files in place, then `--copy-input-files` can unblock them (at a cost in time, space, and IOPS). Lastly, avoid using untrusted WDL code or container images; but if they're necessary, then use a separate EFS access point and restrict the IAM and network configuration for the AWS Batch containers appropriately.

### EFS performance considerations

To scale up to larger workloads, it's important to study AWS documentation on EFS [performance](https://docs.aws.amazon.com/efs/latest/ug/performance.html) and [monitoring](https://docs.aws.amazon.com/efs/latest/ug/monitoring-cloudwatch.html). Like any network file system, EFS limits on throughput and IOPS can cause bottlenecks; and worse, throughput burst credit exhaustion can effectively freeze a workflow.

Management tips:

* Monitor file system throughput limits, IOPS, and burst credits in the EFS area of the AWS Console.
* Create the file system in "Max I/O" performance mode.
* Stage large datasets onto the file system well in advance, increasing the available burst throughput.
* Temporarily provision higher throughput than bursting mode provides (24-hour minimum provisioning commitment).
* Configure miniwdl and AWS Batch to limit the number of concurrent jobs and/or the rate at which they turn over (see [miniwdl_aws.cfg](https://github.com/miniwdl-ext/miniwdl-aws/blob/main/miniwdl_aws.cfg) for relevant details).
* Spread out separate workflow runs over time or across multiple EFS file systems.
* Code WDL tasks to write any purely-temporary files into `$TMPDIR`, which may use local scratch space, instead of the EFS working directory.

### FSx for Lustre and other shared filesystems

If EFS performance remains insufficient, then you can configure your Batch compute environments to automatically mount some other shared filesystem upon instance startup. Then use `miniwdl-aws-submit --no-efs` to make it assume the filesystem will already be mounted at a certain location (default `--mount /mnt/net`) across all instances. In this case, the compute environment for workflow jobs is expected to use EC2 instead of Fargate resources (usually necessary for mounting).

The miniwdl-aws-terraform repo [includes a variant](https://github.com/miniwdl-ext/miniwdl-aws-terraform/tree/main/fsx) setting this up with [FSx for Lustre](https://aws.amazon.com/fsx/lustre/). FSx offers higher throughput scalability, but has other downsides compared to EFS (higher upfront costs, manual capacity scaling, single-AZ deployment, fewer AWS service integrations).

## Logs & troubleshooting

If the terminal log isn't available (through Studio or `miniwdl-submit-awsbatch --follow`) to trace a workflow failure, look for miniwdl's usual log files written in the run directory on EFS or copied to S3.

Each task job's log is also forwarded to [CloudWatch Logs](https://docs.aws.amazon.com/AmazonCloudWatch/latest/logs/WhatIsCloudWatchLogs.html) under the `/aws/batch/job` group and a log stream name reported in miniwdl's log. Using `miniwdl-aws-submit`, the workflow job's log is also forwarded. CloudWatch Logs indexes the logs for structured search through the AWS Console & API.

Misconfigured infrastructure might prevent logs from being written to EFS or CloudWatch at all. In that case, use the AWS Batch console/API to find status messages for the workflow or task jobs.

## Contributing

Pull requests are welcome! For help, open an issue here or drop in on [#miniwdl in the OpenWDL Slack](https://openwdl.slack.com/archives/C02JCRJU79T).

**Code formatting and linting.** To prepare your code to pass the CI checks,

```
pip3 install --upgrade -r test/requirements.txt
pre-commit run --all-files
```

**Running tests.** In an AWS-credentialed terminal session,

```
MINIWDL__AWS__WORKFLOW_QUEUE=miniwdl-workflow test/run_tests.sh
```

This builds the requisite Docker image from the current code revision and pushes it to an ECR repository (which must be prepared once with `aws ecr create-repository --repository-name miniwdl-aws`). To test an image from the [GitHub public registry](https://github.com/miniwdl-ext/miniwdl-aws/pkgs/container/miniwdl-aws) or some other version, set `MINIWDL__AWS__WORKFLOW_IMAGE` to the desired tag.

"""
miniwdl-aws-submit CLI entry point (console script) to submit a miniwdl "workflow job" to an AWS
Batch queue, which will invoke miniwdl-run-s3upload to run the workflow (spawning additional Batch
jobs as needed to execute tasks). This is typically used on-laptop to kick off workflows, without
the laptop needing to stay on/connected. It can also wait for the workflow job to complete and
stream its logs.
"""

import sys
import os
import time
import argparse
import shlex
from datetime import datetime
from collections import defaultdict
import boto3
from ._util import detect_aws_region, randomize_job_name, END_OF_LOG, efs_id_from_access_point


def miniwdl_submit_awsbatch(argv):
    # Configure from arguments/environment/tags
    args, unused_args = parse_args(argv)
    verbose = (
        args.follow or args.self_test or "--verbose" in unused_args or "--debug" in unused_args
    )
    detect_env_args(args)
    if verbose:
        print("Workflow job queue: " + args.workflow_queue, file=sys.stderr)

    aws_region_name = detect_aws_region(None)
    if not aws_region_name:
        print(
            "Failed to detect AWS region; configure AWS CLI or set environment AWS_DEFAULT_REGION",
            file=sys.stderr,
        )
        sys.exit(1)
    aws_batch = boto3.client("batch", region_name=aws_region_name)
    detect_tags_args(aws_batch, args)

    if verbose:
        print("Task job queue: " + args.task_queue, file=sys.stderr)
        if args.efs:
            print("Workflow IAM role ARN: " + args.workflow_role, file=sys.stderr)
            print("EFS Access Point: " + args.fsap, file=sys.stderr)

    fs_id = None
    if args.efs:
        fs_id = efs_id_from_access_point(aws_region_name, args.fsap)
        if verbose:
            print("EFS: " + fs_id, file=sys.stderr)

    # Prepare workflow job: command, environment, and container properties
    job_name, miniwdl_run_cmd, wdl_zip = form_miniwdl_run_cmd(args, unused_args, verbose)
    job_name = randomize_job_name(job_name)
    if verbose:
        print("Workflow job image: " + args.image, file=sys.stderr)
        print("Invocation: " + " ".join(shlex.quote(s) for s in miniwdl_run_cmd), file=sys.stderr)
    (
        workflow_container_props,
        workflow_container_overrides,
        job_def_tags,
        job_tags,
    ) = form_workflow_container_props(args, miniwdl_run_cmd, fs_id, wdl_zip, verbose)

    # Register & submit workflow job
    try:
        workflow_job_def = aws_batch.register_job_definition(
            jobDefinitionName=job_name,
            platformCapabilities=["FARGATE" if args.efs else "EC2"],
            type="container",
            containerProperties=workflow_container_props,
            tags=job_def_tags,
        )
    except BaseException as exc:
        if wdl_zip and "JobDefinition size must be less than" in str(exc):
            print(_WDL_ZIP_SIZE_MSG, file=sys.stderr)
            sys.exit(123)
        raise
    workflow_job_def_handle = (
        f"{workflow_job_def['jobDefinitionName']}:{workflow_job_def['revision']}"
    )
    try:
        workflow_job_id = aws_batch.submit_job(
            jobName=job_name,
            jobQueue=args.workflow_queue,
            jobDefinition=workflow_job_def_handle,
            containerOverrides=workflow_container_overrides,
            tags=job_tags,
        )["jobId"]
        if verbose:
            print(f"Submitted {job_name} to {args.workflow_queue}:", file=sys.stderr)
            sys.stderr.flush()
        print(workflow_job_id)
        if not sys.stdout.isatty():
            print(workflow_job_id, file=sys.stderr)
    finally:
        aws_batch.deregister_job_definition(jobDefinition=workflow_job_def_handle)

    # Wait for workflow job, if requested
    exit_code = 0
    if args.wait or args.follow:
        exit_code = wait(
            aws_region_name,
            aws_batch,
            workflow_job_id,
            args.follow,
            expect_log_eof=not args.self_test,
        )
    sys.exit(exit_code)


def parse_args(argv):
    if "COLUMNS" not in os.environ:
        os.environ["COLUMNS"] = "100"
    parser = argparse.ArgumentParser(
        prog="miniwdl-aws-submit",
        description="Launch `miniwdl run` on AWS Batch (+ EFS at /mnt/efs), itself launching additional"
        " Batch jobs to execute WDL tasks. Passed-through arguments to `miniwdl run` should refer to"
        " s3:// or /mnt/efs/ input paths, rather than the local filesystem.",
        usage="miniwdl-aws-submit [miniwdl_run_arg ...] --workflow-queue WORKFLOW_QUEUE",
        allow_abbrev=False,
    )
    group = parser.add_argument_group("AWS Batch")
    group.add_argument(
        "--workflow-queue",
        help="job queue for workflow job [env MINIWDL__AWS__WORKFLOW_QUEUE]",
    )
    group.add_argument(
        "--task-queue",
        help="job queue for task jobs [env MINIWDL__AWS__TASK_QUEUE"
        " or detect from DefaultTaskQueue tag on workflow job queue]",
    )
    group.add_argument(
        "--task-queue-fallback",
        help="job queue for task jobs following runtime.preemptible spot interruptions [env"
        " MINIWDL__AWS__TASK_QUEUE_FALLBACK or detect from DefaultTaskQueueFallback tag on workflow job queue]",
    )
    group.add_argument(
        "--fsap",
        help="EFS Access Point ID (fsap-xxxx) for mounting [env MINIWDL__AWS__FSAP"
        " or detect from DefaultFsap tag on workflow job queue]",
    )
    group.add_argument(
        "--no-efs",
        "--no-EFS",
        action="store_false",
        dest="efs",
        help="instead of EFS, expect EC2 compute environments to automatically mount some other shared filesystem [env MINIWDL__AWS__FS=0]",
    )
    group.add_argument(
        "--mount",
        default=None,
        help="shared filesystem mount point in all containers [/mnt/efs or /mnt/net]",
    )
    group = parser.add_argument_group("Workflow job provisioning")
    group.add_argument(
        "--workflow-role",
        help="ARN of execution+job role for workflow job [env MINIWDL__AWS__WORKFLOW_ROLE"
        " or detect from WorkflowEngineRoleArn tag on workflow job queue]",
    )
    group.add_argument("--name", help="workflow job name [WDL filename]")
    group.add_argument(
        "--cpu", metavar="N", type=str, default="1", help="vCPUs for workflow job [1]"
    )
    group.add_argument(
        "--memory-GiB", metavar="N", type=int, default=4, help="memory for workflow job [4]"
    )
    group.add_argument(
        "--image",
        help="override miniwdl-aws Docker image tag for workflow job [env MINIWDL__AWS__WORKFLOW_IMAGE]",
    )
    group.add_argument(
        "--no-env", action="store_true", help="don't pass through MINIWDL__* environment variables"
    )
    group.add_argument(
        "--no-public-ip",
        action="store_true",
        help="don't assign public IP (workflow compute env has private subnet & NAT)",
    )
    group = parser.add_argument_group("miniwdl I/O")
    group.add_argument(
        "--dir",
        default=None,
        help="run directory prefix [{mount}/miniwdl_run or {mount}/miniwdl_run]",
    )
    group.add_argument(
        "--s3upload",
        help="s3://bucket/folder/ at which to upload run outputs (otherwise left on shared filesystem)",
    )
    group.add_argument(
        "--delete-after",
        choices=("always", "success", "failure"),
        help="with --s3upload, delete run directory afterwards",
    )
    parser.add_argument(
        "--wait", "-w", action="store_true", help="wait for workflow job to complete"
    )
    parser.add_argument(
        "--follow",
        "-f",
        action="store_true",
        help="live-stream workflow log to standard error (implies --wait)",
    )
    parser.add_argument("--self-test", action="store_true", help="perform `miniwdl run_self_test`")

    args, unused_args = parser.parse_known_args(argv[1:])

    if os.environ.get("MINIWDL__AWS__FS", "").strip().lower() in ("false", "f", "0", "no", "n"):
        args.efs = False
    if not args.mount:
        args.mount = "/mnt/efs" if args.efs else "/mnt/net"
    if args.mount.endswith("/"):
        args.mount = args.mount[:-1]
    assert args.mount
    if not args.dir:
        args.dir = os.path.join(args.mount, "miniwdl_run")
    if not args.dir.startswith(args.mount):
        print(f"--dir must begin with {args.mount}", file=sys.stderr)
        sys.exit(1)

    return (args, unused_args)


def detect_env_args(args):
    """
    Detect configuration set through environment variables (that weren't set by command-line args)
    """
    args.fsap = args.fsap if args.fsap else os.environ.get("MINIWDL__AWS__FSAP", "")
    args.workflow_queue = (
        args.workflow_queue
        if args.workflow_queue
        else os.environ.get("MINIWDL__AWS__WORKFLOW_QUEUE", None)
    )
    if not args.workflow_queue:
        print(
            "--workflow-queue is required (or environment variable MINIWDL__AWS__WORKFLOW_QUEUE)",
            file=sys.stderr,
        )
        sys.exit(1)
    args.fsap = args.fsap if args.fsap else os.environ.get("MINIWDL__AWS__FSAP", "")
    args.task_queue = (
        args.task_queue if args.task_queue else os.environ.get("MINIWDL__AWS__TASK_QUEUE", None)
    )
    if not args.task_queue_fallback:
        args.task_queue_fallback = os.environ.get("MINIWDL__AWS__TASK_QUEUE_FALLBACK", None)
    args.workflow_role = (
        args.workflow_role
        if args.workflow_role
        else os.environ.get("MINIWDL__AWS__WORKFLOW_ROLE", None)
    )
    args.image = args.image if args.image else os.environ.get("MINIWDL__AWS__WORKFLOW_IMAGE", None)
    if not args.image:
        # version-matched default image from our GitHub build
        import importlib_metadata

        try:
            args.image = "ghcr.io/miniwdl-ext/miniwdl-aws:v" + importlib_metadata.version(
                "miniwdl-aws"
            )
        except importlib_metadata.PackageNotFoundError:
            print(
                "Failed to detect miniwdl Docker image version tag; set explicitly with --image or MINIWDL__AWS__WORKFLOW_IMAGE",
                file=sys.stderr,
            )
            sys.exit(1)

    if args.delete_after and not args.s3upload:
        print("--delete-after requires --s3upload", file=sys.stderr)
        sys.exit(1)
    args.s3upload = (
        args.s3upload if args.s3upload else os.environ.get("MINIWDL__AWS__S3_UPLOAD_FOLDER", None)
    )
    args.delete_after = (
        args.delete_after.strip().lower()
        if args.delete_after
        else os.environ.get("MINIWDL__AWS__DELETE_AFTER_S3_UPLOAD", None)
    )


def detect_tags_args(aws_batch, args):
    """
    If not otherwise set by command line arguments or environment, inspect tags of the workflow job
    queue to detect default task job queue and (if applicable) EFS Access Point ID and workflow
    role ARN. Infra provisioning (CloudFormation, Terraform, etc.) may have set the expected tags.
    """
    if not args.task_queue or (args.efs and not (args.fsap or args.workflow_role)):
        workflow_queue_tags = aws_batch.describe_job_queues(jobQueues=[args.workflow_queue])[
            "jobQueues"
        ][0]["tags"]
        if not args.task_queue:
            args.task_queue = workflow_queue_tags.get("DefaultTaskQueue", None)
            if not args.task_queue:
                print(
                    "Unable to detect default task job queue name from DefaultTaskQueue tag of workflow job queue."
                    " Set --task-queue or environment variable MINIWDL__AWS__TASK_QUEUE.",
                    file=sys.stderr,
                )
                sys.exit(1)
        if not args.task_queue_fallback:
            args.task_queue_fallback = workflow_queue_tags.get("DefaultTaskQueueFallback", None)
        if args.efs and not args.fsap:
            try:
                args.fsap = workflow_queue_tags["DefaultFsap"]
                assert args.fsap.startswith("fsap-")
            except:
                if not args.fsap:
                    print(
                        "Unable to detect default EFS Access Point (fsap-xxxx) from DefaultFsap tag of workflow job queue."
                        " Set --fsap or environment variable MINIWDL__AWS__FSAP.",
                        file=sys.stderr,
                    )
                    sys.exit(1)
        if args.efs and not args.workflow_role:
            # Workflow role ARN is needed for Fargate Batch (unlike EC2 Batch, where a role is
            # associated with the EC2 instance profile in the compute environment).
            try:
                args.workflow_role = aws_batch.describe_job_queues(jobQueues=[args.workflow_queue])[
                    "jobQueues"
                ][0]["tags"]["WorkflowEngineRoleArn"]
                assert args.workflow_role.startswith("arn:aws:iam::")
            except:
                if not args.workflow_role:
                    print(
                        "Unable to detect ARN of workflow engine IAM role from WorkflowEngineRoleArn tag of workflow job queue."
                        " Double-check --workflow-queue, or set --workflow-role or environment MINIWDL__AWS__WORKFLOW_ROLE.",
                        file=sys.stderr,
                    )
                    sys.exit(1)


def form_miniwdl_run_cmd(args, unused_args, verbose=False):
    """
    Formulate the `miniwdl run` command line to be invoked in the workflow job container
    """
    wdl_zip = None
    if args.self_test:
        self_test_dir = os.path.join(
            args.mount, "miniwdl_run_self_test", datetime.today().strftime("%Y%m%d_%H%M%S")
        )
        miniwdl_run_cmd = ["miniwdl", "run_self_test", "--dir", self_test_dir]
        job_name = args.name if args.name else "miniwdl_run_self_test"
    else:
        wdl_filename_pos = next(
            (i for i, arg in enumerate(unused_args) if not arg.startswith("-")), -1
        )
        if wdl_filename_pos < 0:
            print("Command line appears to be missing WDL filename", file=sys.stderr)
            sys.exit(1)
        wdl_filename = unused_args[wdl_filename_pos]
        wdl_zip = zip_wdl(wdl_filename, args.mount, verbose)
        if wdl_zip:
            # this sentinel argument will be recognized by miniwdl-run-s3upload
            unused_args[wdl_filename_pos] = "--WDL--ZIP--"
        job_name = args.name
        if not job_name:
            job_name = os.path.basename(wdl_filename).lstrip(".")
            try:
                for punct in (".", "?"):
                    if job_name.index(punct) > 0:
                        job_name = job_name[: job_name.index(punct)]
            except ValueError:
                pass
            job_name = ("miniwdl_run_" + job_name)[:128]
        # pass most arguments through to miniwdl-run-s3upload inside workflow job
        miniwdl_run_cmd = ["miniwdl-run-s3upload"] + unused_args
        miniwdl_run_cmd.extend(["--dir", args.dir])
        miniwdl_run_cmd.extend(["--s3upload", args.s3upload] if args.s3upload else [])
        miniwdl_run_cmd.extend(["--delete-after", args.delete_after] if args.delete_after else [])
    return (job_name, miniwdl_run_cmd, wdl_zip)


def zip_wdl(wdl_filename, mount, verbose=False):
    """
    If wdl_filename is an existing local .wdl or .zip file, prepare to ship it as the WDL source
    code for the workflow job to execute. (Otherwise, it'll be passed through assuming it's some
    path or URI the workflow job will be able to open directly.)

    If it's a .zip file, assume it's generated by `miniwdl zip`.

    If it's a .wdl file, run `miniwdl zip` on it.
    """
    if not os.path.isfile(wdl_filename) or not (
        wdl_filename.endswith(".wdl") or wdl_filename.endswith(".zip")
    ):
        if verbose:
            print(
                f"WDL: {wdl_filename} (not a local WDL file; assuming accessible inside workflow job)"
            )
        return None
    if os.path.normpath(os.path.abspath(wdl_filename)).startswith(mount + "/"):
        if verbose:
            print(f"WDL: {wdl_filename} (assuming {mount} accessible inside workflow job)")
        return None

    # load zip bytes
    if wdl_filename.endswith(".wdl"):
        import subprocess
        import tempfile

        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                zip_fn = os.path.join(tmpdir, os.path.basename(wdl_filename)) + ".zip"
                subprocess.check_call(["miniwdl", "zip", "-o", zip_fn, wdl_filename])
                with open(zip_fn, "rb") as zip_file:
                    zip_bytes = zip_file.read()
                # TODO: detect -i file.json in unused_args and provide it to miniwdl zip too
        except subprocess.CalledProcessError as exn:
            sys.exit(exn.returncode)
    else:
        assert wdl_filename.endswith(".zip")
        with open(wdl_filename, "rb") as zip_file:
            zip_bytes = zip_file.read()
    assert zip_bytes, "empty WDL zip"

    # aggressively compress, to maximize chance of fitting within the 30KiB limit on Batch
    # SubmitJob request: https://docs.aws.amazon.com/batch/latest/userguide/service_limits.html
    import base64
    import lzma

    zip_str = base64.urlsafe_b64encode(
        lzma.compress(zip_bytes, format=lzma.FORMAT_ALONE, preset=(9 | lzma.PRESET_EXTREME))
    ).decode("ascii")
    if verbose:
        print(
            f"WDL/ZIP: {wdl_filename} (encoded as {len(zip_str)} bytes to submit with workflow job)",
            file=sys.stderr,
        )
    return zip_str


def form_workflow_container_props(args, miniwdl_run_cmd, fs_id, wdl_zip=None, verbose=False):
    environment = [
        {"name": "MINIWDL__AWS__TASK_QUEUE", "value": args.task_queue},
        {"name": "MINIWDL__FILE_IO__ROOT", "value": args.mount},
    ]
    if args.task_queue_fallback:
        environment.append(
            {"name": "MINIWDL__AWS__TASK_QUEUE_FALLBACK", "value": args.task_queue_fallback}
        )
    if args.efs:
        environment.append({"name": "MINIWDL__AWS__FS", "value": fs_id})
        environment.append({"name": "MINIWDL__AWS__FSAP", "value": args.fsap})
    else:
        environment.append(
            {"name": "MINIWDL__SCHEDULER__CONTAINER_BACKEND", "value": "aws_batch_job_no_efs"}
        )
    extra_env = set()
    if not args.no_env:
        # pass through environment variables starting with MINIWDL__ (except those specific to
        # workflow job launch, or passed through via command line)
        for k in os.environ:
            if k.startswith("MINIWDL__") and k not in (
                "MINIWDL__AWS__FS",
                "MINIWDL__AWS__FSAP",
                "MINIWDL__AWS__TASK_QUEUE",
                "MINIWDL__AWS__TASK_QUEUE_FALLBACK",
                "MINIWDL__AWS__WORKFLOW_QUEUE",
                "MINIWDL__AWS__WORKFLOW_ROLE",
                "MINIWDL__AWS__WORKFLOW_IMAGE",
                "MINIWDL__AWS__S3_UPLOAD_FOLDER",
                "MINIWDL__AWS__S3_UPLOAD_DELETE_AFTER",
                "MINIWDL__FILE_IO__ROOT",
            ):
                environment.append({"name": k, "value": os.environ[k]})
                extra_env.add(k)

    if verbose and extra_env:
        print(
            "Passing through environment variables (--no-env to disable): "
            + " ".join(list(extra_env)),
            file=sys.stderr,
        )

    workflow_container_props = {
        "image": args.image,
        "resourceRequirements": [
            {"type": "VCPU", "value": str(args.cpu)},
            {"type": "MEMORY", "value": str(args.memory_GiB * 1024)},
        ],
        "environment": [],
    }
    job_def_tags = {}
    job_tags = {}
    if wdl_zip:
        # If the command line provided a local WDL (or WDL zipped by `miniwdl zip`), ship it in the
        # workflow job environment, to be picked up by miniwdl-run-s3upload. If the encoded zip is
        # over 4096 characters, then spray the remainder across tags on the workflow job definition
        # and workflow job itself. The 4KiB keeps our container properties (+overrides) within AWS'
        # 8KiB limit. Then we use up to 42 tags on the job def & job, each with 381 usable bytes
        # (within the AWS limits of 50 tags per resource with key length 128 and value length 256).
        # Total capacity = 4096 + 2*42*381 = 36100 characters.
        workflow_container_props["environment"].append({"name": "WDL_ZIP", "value": wdl_zip[:4096]})
        wdl_zip = wdl_zip[4096:]
        tag_num = 0
        while wdl_zip:
            if tag_num >= 84:
                print(_WDL_ZIP_SIZE_MSG, file=sys.stderr)
                sys.exit(123)
            tag_key = (
                "WZ"
                + chr((ord("A") if tag_num % 42 < 26 else (ord("a") - 26)) + tag_num % 42)
                + wdl_zip[:125]
            )
            tag_value = wdl_zip[125:381]
            wdl_zip = wdl_zip[381:]
            if tag_num < 42:
                job_def_tags[tag_key] = tag_value
            else:
                job_tags[tag_key] = tag_value
            tag_num += 1
    workflow_container_overrides = {
        "command": miniwdl_run_cmd,
        "environment": environment,
    }
    if args.efs:
        # EFS: set EFS volume/mountPoint and Fargate execution role
        assert args.workflow_role and fs_id and args.fsap
        workflow_container_props.update(
            {
                "fargatePlatformConfiguration": {"platformVersion": "1.4.0"},
                "executionRoleArn": args.workflow_role,
                "jobRoleArn": args.workflow_role,
                "volumes": [
                    {
                        "name": "efs",
                        "efsVolumeConfiguration": {
                            "fileSystemId": fs_id,
                            "transitEncryption": "ENABLED",
                            "authorizationConfig": {"accessPointId": args.fsap},
                        },
                    }
                ],
                "mountPoints": [{"containerPath": args.mount, "sourceVolume": "efs"}],
            }
        )
        if not args.no_public_ip:
            workflow_container_props["networkConfiguration"] = {"assignPublicIp": "ENABLED"}
    else:
        # non-EFS: set volume/mountPoint assuming compute environments mount automatically
        workflow_container_props.update(
            {
                "volumes": [
                    {
                        "name": "file_io_root",
                        "host": {"sourcePath": args.mount},
                    }
                ],
                "mountPoints": [{"containerPath": args.mount, "sourceVolume": "file_io_root"}],
            }
        )

    return (workflow_container_props, workflow_container_overrides, job_def_tags, job_tags)


def wait(aws_region_name, aws_batch, workflow_job_id, follow, expect_log_eof=True):
    """
    Wait for workflow job to complete & return its exit code; optionally tail its log to stderr
    """
    try:
        log_follower = None
        exit_code = None
        saw_end = False
        while exit_code is None:
            time.sleep(1.0)
            job_descs = aws_batch.describe_jobs(jobs=[workflow_job_id])
            job_desc = job_descs["jobs"][0]
            if (
                not log_follower
                and "container" in job_desc
                and "logStreamName" in job_desc["container"]
            ):
                log_stream_name = job_desc["container"]["logStreamName"]
                print("Log stream: " + log_stream_name, file=sys.stderr)
                sys.stderr.flush()
                log_follower = CloudWatchLogsFollower(
                    boto3.DEFAULT_SESSION, aws_region_name, "/aws/batch/job", log_stream_name
                )
            if follow and log_follower:
                for event in log_follower.new_events():
                    if END_OF_LOG not in event["message"]:
                        print(event["message"], file=sys.stderr)
                    else:
                        saw_end = True
                sys.stderr.flush()
            if job_desc["status"] == "SUCCEEDED":
                exit_code = 0
            elif job_desc["status"] == "FAILED":
                exit_code = -1
                if "container" in job_desc and "exitCode" in job_desc["container"]:
                    exit_code = job_desc["container"]["exitCode"]
                    assert exit_code != 0
        if expect_log_eof and follow and log_follower and not saw_end:
            # give straggler log messages a few seconds to appear
            time.sleep(3.0)
            for event in log_follower.new_events():
                if END_OF_LOG not in event["message"]:
                    print(event["message"], file=sys.stderr)
                else:
                    saw_end = True
            if not saw_end:
                print(
                    f"[miniwdl-aws-submit] WARNING: end-of-log marker not seen; more information may appear in log stream {log_stream_name}",
                    file=sys.stderr,
                )
            sys.stderr.flush()
        status = job_desc["status"]
        reason = job_desc.get("statusReason", "")
        if reason:
            reason = (
                f"\t{reason}" if reason and reason != "Essential container in task exited" else ""
            )
        print(status + "\t" + workflow_job_id + reason, file=sys.stderr)
        if status == "FAILED" and "Container Overrides length must be at most" in reason:
            print(_WDL_ZIP_SIZE_MSG, file=sys.stderr)
            exit_code = 123
        assert isinstance(exit_code, int) and (exit_code != 0 or status == "SUCCEEDED")
        return exit_code
    except KeyboardInterrupt:
        print(
            f"[miniwdl-aws-submit] interrupted by Ctrl-C; workflow may remain active in workflow job {workflow_job_id}",
            file=sys.stderr,
        )
        return -1


class CloudWatchLogsFollower:
    # Based loosely on:
    #   https://github.com/aws/aws-cli/blob/v2/awscli/customizations/logs/tail.py
    # which wasn't suitable to use directly at the time of this writing, because of
    #   https://github.com/aws/aws-cli/issues/5560
    def __init__(self, boto_session, region_name, group_name, stream_name=None):
        self.group_name = group_name
        self.stream_name = stream_name
        self._newest_timestamp = None
        self._newest_event_ids = set()
        self._client = boto_session.client("logs", region_name=region_name)

    def new_events(self):
        event_ids_per_timestamp = defaultdict(set)

        filter_args = {"logGroupName": self.group_name}
        if self.stream_name:
            filter_args["logStreamNames"] = [self.stream_name]
        if self._newest_timestamp:
            filter_args["startTime"] = self._newest_timestamp
        while True:
            try:
                response = self._client.filter_log_events(**filter_args)
            except self._client.exceptions.ResourceNotFoundException:
                return  # we may learn the Batch job's log stream name before it actually exists
            for event in response["events"]:
                # For the case where we've hit the last page, we will be
                # reusing the newest timestamp of the received events to keep polling.
                # This means it is possible that duplicate log events with same timestamp
                # are returned back which we do not want to yield again.
                # We only want to yield log events that we have not seen.
                if event["eventId"] not in self._newest_event_ids:
                    event_ids_per_timestamp[event["timestamp"]].add(event["eventId"])
                    yield event
            if "nextToken" in response:
                filter_args["nextToken"] = response["nextToken"]
            else:
                break

        if event_ids_per_timestamp:
            self._newest_timestamp = max(event_ids_per_timestamp.keys())
            self._newest_event_ids = event_ids_per_timestamp[self._newest_timestamp]


_WDL_ZIP_SIZE_MSG = (
    "\nExceeded AWS Batch request payload size limit; make the WDL source code and/or inputs"
    " available by URL or remote filesystem path, to pass by reference."
)

"""
miniwdl-aws-status CLI entry point (console script) to report status of jobs submited to
miniwdl "workflow job" to an AWS Batch queue with miniwdl-aws-submit CLI.
"""
import sys
import os
import argparse
from datetime import datetime
import boto3
from botocore.exceptions import ClientError
from miniwdl_aws._util import detect_aws_region

DEFAULT_WORKFLOW_QUEUE = "miniwdl-workflow"


def miniwdl_aws_status(argv=sys.argv):

    # Configure from arguments/environment/tags
    args, unused_args = parse_args(argv)
    detect_env_args(args)
    if args.debug:
        print("Workflow job queue: " + args.workflow_queue, file=sys.stderr)

    aws_region_name = detect_aws_region(None)
    if not aws_region_name:
        print(
            "Failed to detect AWS region; configure AWS CLI or set environment AWS_DEFAULT_REGION",
            file=sys.stderr,
        )
        sys.exit(1)
    aws_batch = boto3.client("batch", region_name=aws_region_name)
    aws_logs = boto3.client("logs", region_name=aws_region_name)
    detect_tags_args(aws_batch, args)

    if args.command == "all":
        # print top level status of all submited tasks
        queue_status(aws_batch, args)
    elif args.command == "job":
        # status   for a specific workflow job
        job_status(aws_batch, aws_logs, args)
    elif args.command == "log":
        # log for a specific workflow job
        job_log(aws_batch, aws_logs, args)

    return 0


def parse_args(argv):

    parser = argparse.ArgumentParser(
        prog="miniwdl-aws-status",
        description="report status of miniwdl jobs submited to AWS Batch with miiwdl-aws-submit",
        usage="miniwdl-aws-status [optional_arguments]",
        allow_abbrev=False,
    )

    parser.add_argument(
        "command",
        nargs="?",  # to make it optional: https://stackoverflow.com/questions/4480075/argparse-optional-positional-arguments
        default="all",
        choices=["all", "job", "log"],
        help="type of status  to report",
    )

    parser.add_argument(
        "--debug",
        "-d",
        action="store_true",
        help="flag to generate debug information",
    )

    parser.add_argument(
        "--exclude-debug-log",
        action="store_true",
        help="flag to exclude DEBUG  messages from LOG printout",
    )

    group = parser.add_argument_group("Jobs identification")
    group.add_argument(
        "--job-id",
        default=None,
        help="jobId to identify specific job. Use tha latest submited job if ommited",
    )
    # group.add_argument(
    #     "--job-name",
    #     default=None,
    #     help="jobName to identify specific job",
    # )

    group = parser.add_argument_group("AWS Batch")
    group.add_argument(
        "--workflow-queue",
        help="job queue for workflow job [env MINIWDL__AWS__WORKFLOW_QUEUE]",
    )
    group.add_argument(
        "--task-queue",
        help="job queue for task jobs [env MINIWDL__AWS__TASK_QUEUE"
        " or detect from DefaultTaskQueue tag on workflow job queue",
    )

    args, unused_args = parser.parse_known_args(argv[1:])

    return (args, unused_args)


def detect_env_args(args):
    """
    Detect configuration set through environment variables (that weren't set by command-line args)
    """
    args.workflow_queue = (
        args.workflow_queue
        if args.workflow_queue
        else os.environ.get("MINIWDL__AWS__WORKFLOW_QUEUE", DEFAULT_WORKFLOW_QUEUE)
    )
    if not args.workflow_queue:
        print(
            "--workflow-queue is required (or environment variable MINIWDL__AWS__WORKFLOW_QUEUE)",
            file=sys.stderr,
        )
        sys.exit(1)
    args.task_queue = (
        args.task_queue if args.task_queue else os.environ.get("MINIWDL__AWS__TASK_QUEUE", None)
    )


def detect_tags_args(aws_batch, args):
    """
    If not otherwise set by command line arguments or environment, inspect tags of the workflow job
    queue to detect default EFS Access Point ID (fsap-xxx), task job queue, and/or workflow role
    ARN. Infra provisioning (CloudFormation, Terraform, etc.) may have set the respective tags.
    """
    if not (args.task_queue):
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


def get_jobs(aws_batch, queue, status):
    try:
        job_paginator = aws_batch.get_paginator("list_jobs")
        job_page_iterator = job_paginator.paginate(jobQueue=queue, jobStatus=status)
    except ClientError as exe:
        print("Unable to get jobs list: %s", str(exe), file=sys.stderr)
        sys.exit(1)

    result = list()
    for page in job_page_iterator:
        result += page["jobSummaryList"]

    job_list = list()
    for item in result:
        job_list.append(
            {
                "jobId": item["jobId"],
                "jobName": item["jobName"],
                "status": item["status"],
                "statusReason": item["statusReason"] if "statusReason" in item else None,
                "createdAt": item["createdAt"] if "createdAt" in item else None,
                "stoppedAt": item["stoppedAt"] if "stoppedAt" in item else None,
            }
        )

    return job_list


def print_jobs(jobs):
    for job in jobs:
        start_time_str = (
            datetime.utcfromtimestamp(job["createdAt"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
            if job["createdAt"]
            else "----"
        )
        stop_time_str = (
            datetime.utcfromtimestamp(job["stoppedAt"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
            if job["stoppedAt"]
            else "----"
        )
        print(
            f"{job['jobId']}\t{job['status']}\t{start_time_str}\t{stop_time_str}"
            f"\t{job['jobName']}"
        )


def queue_status(aws_batch, args):
    # get list of all runnig jobs
    # aws batch list-jobs --job-queue miniwdl-workflow --job-status RUNNING
    waiting_status = ["SUBMITTED", "PENDING", "RUNNABLE", "STARTING"]
    active_status = ["RUNNING", "SUCCEEDED", "FAILED"]

    waiting_jobs = []
    active_jobs = []

    for status in waiting_status:
        jobs = get_jobs(aws_batch, args.workflow_queue, status)
        waiting_jobs += jobs
    # sort the  list in descdending order of creation date
    if len(waiting_jobs) > 0:
        waiting_jobs = sorted(waiting_jobs, reverse=True, key=lambda d: d["createdAt"])
        print("WAITING jobs:")
        print_jobs(waiting_jobs)

    for status in active_status:
        jobs = get_jobs(aws_batch, args.workflow_queue, status)
        active_jobs += jobs
    # sort the  list in descdending order of creation date
    if len(active_jobs) > 0:
        active_jobs = sorted(active_jobs, reverse=True, key=lambda d: d["createdAt"])
        print("ACTIVE jobs:")
        print_jobs(active_jobs)


def get_latest_job(aws_batch, args):
    """return ID of teh latest submited job"""
    all_status = ["SUBMITTED", "PENDING", "RUNNABLE", "STARTING", "RUNNING", "SUCCEEDED", "FAILED"]
    all_jobs = []
    for status in all_status:
        jobs = get_jobs(aws_batch, args.workflow_queue, status)
        all_jobs += jobs

    if len(all_jobs) == 0:
        return None

    all_jobs = sorted(all_jobs, reverse=True, key=lambda d: d["createdAt"])
    return all_jobs[0]["jobId"]


def job_status(aws_batch, aws_logs, args):

    if not args.job_id:
        print(
            "[WARNING] No --job-id parameter specified, use the latest submited job",
            file=sys.stderr,
        )
        args.job_id = get_latest_job(aws_batch, args)

    # get job  description
    job_description = aws_batch.describe_jobs(jobs=[args.job_id])
    if "jobs" not in job_description:
        print(f"can't find job  description for {args.job_id}", file=sys.stderr)
        sys.exit(1)

    job = job_description["jobs"][0]
    # print job  description
    print(f"jobId       : {job['jobId']}")
    print(f"jobName     : {job['jobName']}")
    print(f"status      : {job['status']}")
    print(f"statusReason: {job['statusReason']}")
    if "createdAt" in job:
        print(
            f"createdAt   : {datetime.utcfromtimestamp(job['createdAt']/1000).strftime('%Y-%m-%d %H:%M:%S')}"
        )
    if "startedAt" in job:
        print(
            f"statrteAt   : {datetime.utcfromtimestamp(job['startedAt']/1000).strftime('%Y-%m-%d %H:%M:%S')}"
        )
    if "startedAt" in job:
        print(
            f"stoppedAt   : {datetime.utcfromtimestamp(job['stoppedAt']/1000).strftime('%Y-%m-%d %H:%M:%S')}"
        )

    container = job["container"]
    logStreamName = container["logStreamName"]
    print(f"logStream   : {logStreamName}")
    command = container["command"]
    cli = " ".join(command)
    print(f"command     : {cli}")

    # extract  sub-tasks IDs from logs
    task_id = []
    logs = get_log(aws_logs, logStreamName)
    for item in logs:
        message = item["message"]
        if "AWS Batch job submitted" in message:
            substring_after = message.split("jobId:", 1)[1]
            jobId = substring_after.split('"', 2)[1]
            task_id.append(jobId)
    # print list of subtask
    if len(task_id) > 0:
        print(f"Sub-tasks   : {len(task_id)}")
        task_description = aws_batch.describe_jobs(jobs=task_id)
        if "jobs" not in job_description:
            print(f"can't find job  description for {task_id}", file=sys.stderr)
            sys.exit(1)

        tasks = task_description["jobs"]
        for task in tasks:
            start_time_str = (
                datetime.utcfromtimestamp(task["createdAt"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
                if "createdAt" in task
                else "----"
            )
            stop_time_str = (
                datetime.utcfromtimestamp(task["stoppedAt"] / 1000).strftime("%Y-%m-%d %H:%M:%S")
                if "stoppedAt" in task
                else "----"
            )
            print(
                f"{task['jobId']}\t{task['status']}\t{start_time_str}\t{stop_time_str}"
                f"\t{task['jobName']}"
            )


def get_log(aws_logs, logStreamName):
    # try:
    #     job_paginator = aws_logs.get_paginator('get_log_events')
    #     job_page_iterator = job_paginator.paginate(
    #             logGroupName='/aws/batch/job',logStreamName=logStreamName,startFromHead=True
    #             )
    # except ClientError as exe:
    #     print('Unable to get log  events: %s', str(exe),file=sys.stderr)
    #     sys.exit(1)

    # result = list()
    # for page in job_page_iterator:
    #     result += page['events']

    have_more = True
    nextToken = None

    results = []

    while have_more:
        if not nextToken:
            logs = aws_logs.get_log_events(
                logGroupName="/aws/batch/job", logStreamName=logStreamName, startFromHead=True
            )
        else:
            logs = aws_logs.get_log_events(
                logGroupName="/aws/batch/job",
                logStreamName=logStreamName,
                startFromHead=True,
                nextToken=nextToken,
            )

        results += logs["events"]

        if ("nextForwardToken" in logs) and (nextToken != logs["nextForwardToken"]):
            nextToken = logs["nextForwardToken"]
        else:
            have_more = False

    return results


def job_log(aws_batch, aws_logs, args):
    """print log associated with this task"""
    if not args.job_id:
        print(
            "[WARNING] No --job-id parameter specified, use the latest submited job",
            file=sys.stderr,
        )
        args.job_id = get_latest_job(aws_batch, args)

    # get job  description
    job_description = aws_batch.describe_jobs(jobs=[args.job_id])
    if "jobs" not in job_description:
        print(f"can't find job  description for {args.job_id}", file=sys.stderr)
        sys.exit(1)
    job = job_description["jobs"][0]
    container = job["container"]
    logStreamName = container["logStreamName"]

    print(f"LOG FOR JOB {job['jobId']} ({job['jobName']}) ")
    logs = get_log(aws_logs, logStreamName)
    for item in logs:
        message = item["message"]
        if args.exclude_debug_log and (" DEBUG " in message[:80]):
            continue
        print(message)


if __name__ == "__main__":
    sys.exit(miniwdl_aws_status())

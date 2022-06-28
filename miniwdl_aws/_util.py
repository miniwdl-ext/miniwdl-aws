import os
import boto3
import base64
import json
import uuid
import requests
import subprocess
from WDL._util import StructuredLogMessage as _


def detect_aws_region(cfg):
    if cfg and cfg.has_option("aws", "region") and cfg.get("aws", "region"):
        return cfg.get("aws", "region")

    # check environment variables
    for ev in ("AWS_REGION", "AWS_DEFAULT_REGION"):
        if os.environ.get(ev):
            return os.environ[ev]

    # check boto3, which will load ~/.aws
    if boto3.DEFAULT_SESSION and boto3.DEFAULT_SESSION.region_name:
        return boto3.DEFAULT_SESSION.region_name
    session = boto3.Session()
    if session.region_name:
        return session.region_name

    # query EC2 metadata
    try:
        return requests.get(
            "http://169.254.169.254/latest/meta-data/placement/region", timeout=2.0
        ).text
    except:
        pass

    return None


def randomize_job_name(job_name):
    # Append entropy to the Batch job name to avoid race condition using identical names in
    # concurrent RegisterJobDefinition requests
    return (
        job_name[:103]  # 119 + 1 + 8 = 128
        + "-"
        + base64.b32encode(uuid.uuid4().bytes[:5]).lower().decode()
    )


def efs_id_from_access_point(region_name, fsap_id):
    # Resolve the EFS access point id (fsap-xxxx) to the associated file system id (fs-xxxx). Saves
    # user from having to specify both.
    aws_efs = boto3.Session().client("efs", region_name=region_name)
    desc = aws_efs.describe_access_points(AccessPointId=fsap_id)
    assert len(desc.get("AccessPoints", [])) == 1
    desc = desc["AccessPoints"][0]
    fs_id = desc["FileSystemId"]
    assert isinstance(fs_id, str) and fs_id.startswith("fs-")
    return fs_id


def detect_sagemaker_studio_efs(logger, **kwargs):
    # Detect if we're operating inside SageMaker Studio and if so, record EFS mount details
    METADATA_FILE = "/opt/ml/metadata/resource-metadata.json"
    metadata = None
    try:
        with open(METADATA_FILE) as infile:
            metadata = json.load(infile)
        assert metadata["DomainId"] and metadata["UserProfileName"]
    except:
        return None
    try:
        api = boto3.client("sagemaker", **kwargs)
        domain = api.describe_domain(DomainId=metadata["DomainId"])
        efs_id = domain["HomeEfsFileSystemId"]
        profile = api.describe_user_profile(
            DomainId=metadata["DomainId"], UserProfileName=metadata["UserProfileName"]
        )
        efs_uid = profile["HomeEfsFileSystemUid"]
        assert efs_id and efs_uid
        efs_home = f"/{efs_uid}"  # home directory on EFS
        efs_mount = os.getenv("HOME")  # where the EFS home directory is mounted inside Studio
        logger.notice(
            _(
                "detected SageMaker Studio",
                domain=metadata["DomainId"],
                user=metadata["UserProfileName"],
                efs_id=efs_id,
                efs_home=efs_home,
                efs_mount=efs_mount,
            )
        )
        return (efs_id, efs_uid, efs_home, efs_mount)
    except Exception as exn:
        logger.warning(
            _(
                "detected local AWS SageMaker Studio metadata, but failed to query domain",
                error=str(exn),
                metadata=METADATA_FILE,
                domain=metadata["DomainId"],
                user=metadata["UserProfileName"],
            )
        )
        return None


def detect_studio_fsap(logger, efs_id, efs_uid, efs_home, **kwargs):
    # Look for an Access Point with the appropriate configuration to mount the SageMaker Studio EFS
    # (in the same way it's presented through Studio)
    try:
        efs = boto3.client("efs", **kwargs)
        access_points = efs.describe_access_points(FileSystemId=efs_id, MaxResults=100).get(
            "AccessPoints", []
        )
        if len(access_points) >= 100:
            logger.warn(
                _(
                    "EFS has >=100 Access Points; set configuration [aws] fsap or environment MINIWDL__AWS__FSAP to avoid searching through them",
                    efs_id=efs_id,
                )
            )
        for ap in access_points:
            assert ap["FileSystemId"] == efs_id
            if (
                ap["LifeCycleState"] == "available"
                and ap.get("RootDirectory", {}).get("Path", "") == efs_home
                and str(ap.get("PosixUser", {}).get("Uid", "")) == efs_uid
            ):
                logger.notice(
                    _(
                        "detected suitable EFS Access Point; to override, set configuration [aws] fsap or environment MINIWDL__AWS__FSAP",
                        arn=ap["AccessPointArn"],
                    )
                )
                return ap["AccessPointId"]
        return None
    except Exception as exn:
        logger.warning(
            _(
                "error detecting EFS Access Point",
                error=str(exn),
                efs_id=efs_id,
                ufs_uid=efs_uid,
            )
        )
        return None


def detect_gwfcore_batch_queue(logger, efs_id, **kwargs):
    # Look for a Batch job queue tagged with the Studio EFS id (indicating it's our default)
    try:
        batch = boto3.client("batch", **kwargs)
        queues = batch.describe_job_queues(maxResults=100).get("jobQueues", [])
        if len(queues) >= 100:
            logger.warn(
                "AWS Batch has >=100 job queues; set configuration [aws] task_queue or environment MINIWDL__AWS__TASK_QUEUE to avoid searching through them"
            )
        queues = [
            q
            for q in queues
            if q.get("state", "") == "ENABLED"
            and q.get("status", "") == "VALID"
            and q.get("tags", {}).get("MiniwdlStudioEfsId", "") == efs_id
        ]
        if not queues:
            return None
        if len(queues) > 1:
            default_queues = [q for q in queues if q.get("jobQueueName", "").startswith("default-")]
            if default_queues:
                queues = default_queues
        logger.notice(
            _(
                "detected suitable AWS Batch job queue; to override, set configuration [aws] task_queue or environment MINIWDL__AWS__TASK_QUEUE",
                arn=queues[0]["jobQueueArn"],
            )
        )
        return queues[0]["jobQueueName"]
    except Exception as exn:
        logger.warning(
            _(
                "error detecting AWS Batch job queue",
                error=str(exn),
                efs_id=efs_id,
            )
        )
        return None


def subprocess_run_with_clean_exit(*args, check=False, **kwargs):
    """
    As subprocess.run(*args, **kwargs), but in the event of a SystemExit, KeyboardInterrupt, or
    BrokenPipe exception, sends SIGTERM to the subprocess and waits for it to exit before
    re-raising. Typically paired with signal handlers for SIGTERM/SIGINT/etc. to raise SystemExit.
    """

    assert "timeout" not in kwargs
    with subprocess.Popen(*args, **kwargs) as subproc:
        while True:
            try:
                stdout, stderr = subproc.communicate(timeout=0.1)
                assert isinstance(subproc.returncode, int)
                completed = subprocess.CompletedProcess(
                    subproc.args, subproc.returncode, stdout, stderr
                )
                if check:
                    completed.check_returncode()
                return completed
            except (SystemExit, KeyboardInterrupt, BrokenPipeError):
                subproc.terminate()
                subproc.communicate()
                raise
            except subprocess.TimeoutExpired:
                pass


END_OF_LOG = "[miniwdl_run_s3upload] -- END OF LOG --"

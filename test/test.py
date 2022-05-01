import os
import json
import subprocess
import time
import pytest
import boto3
from datetime import datetime
from urllib.parse import urlparse

assert "AWS_DEFAULT_REGION" in os.environ
assert (
    "MINIWDL__AWS__WORKFLOW_IMAGE" in os.environ
    and "miniwdl-aws" in os.environ["MINIWDL__AWS__WORKFLOW_IMAGE"]
), "set environment MINIWDL__AWS__WORKFLOW_IMAGE to repo:digest"
assert (
    "MINIWDL__AWS__WORKFLOW_QUEUE" in os.environ
), "set MINIWDL__AWS__WORKFLOW_QUEUE to Batch queue name"
assert (
    "MINIWDL_AWS_TEST_BUCKET" in os.environ
), "set MINIWDL_AWS_TEST_BUCKET to test S3 bucket (name only)"


@pytest.fixture(scope="module")
def aws_batch():
    return boto3.client("batch", region_name=os.environ["AWS_DEFAULT_REGION"])


def batch_miniwdl(aws_batch, args, env=None, upload=None, cache=False):
    """
    Submit & await a Batch job to run cmd in the miniwdl_aws container (usually ~miniwdl run~
    to launch other Batch jobs in turn)
    """
    cmd = ["python3", "-m", "miniwdl_aws"]
    cmd.extend(args)
    cmd.append("--follow")
    if not cache:
        cmd.append("--no-cache")
    if upload:
        if not upload.endswith("/"):
            upload += "/"
        cmd.extend(["--s3upload", upload])

    exit_code = subprocess.run(
        cmd, cwd=os.path.dirname(os.path.dirname(__file__)), check=False, env=env
    ).returncode

    if exit_code != 0:
        ans = {"success": False, "exit_code": exit_code}
        if upload:
            error = get_s3uri(upload + "error.json")
            if error:
                ans["error"] = json.loads(error)
        return ans

    ans = {"success": True}
    if upload:
        outputs = get_s3uri(upload + "outputs.json")
        if outputs:
            ans["outputs"] = json.loads(outputs)
    return ans


def get_s3uri(uri):
    """
    Download bytes from s3:// URI
    """
    try:
        assert uri.startswith("s3://")
        parts = urlparse(uri)
        obj = boto3.resource("s3", region_name=os.environ["AWS_DEFAULT_REGION"]).Object(
            parts.netloc, parts.path.lstrip("/")
        )
        return obj.get()["Body"].read()
    except Exception as exn:
        if "NoSuchKey" in str(exn):
            return None
        raise


def test_miniwdl_run_self_test(aws_batch):
    subprocess.run(
        [
            "python3",
            "-m",
            "miniwdl_aws",
            "--follow",
            "--self-test",
            "--no-cache",
            "--mount",
            "/mnt/shared",
        ],
        cwd=os.path.dirname(os.path.dirname(__file__)),
        check=True,
    )


@pytest.fixture(scope="session")
def test_s3_folder():
    """
    S3 folder for this test session
    """
    return f"s3://{os.environ['MINIWDL_AWS_TEST_BUCKET']}/{datetime.today().strftime('%Y%m%d_%H%M%S')}/"


def test_retry_streams(aws_batch, test_s3_folder):
    env = dict(os.environ)
    env["MINIWDL__AWS__RETRY_WAIT"] = "1"
    rslt = batch_miniwdl(
        aws_batch,
        [
            "/var/miniwdl_aws_test_assets/test_retry_streams.wdl",
            "--dir",
            "/mnt/efs/miniwdl_aws_tests",
            "--verbose",
        ],
        upload=test_s3_folder + "test_retry_streams/",
        env=env,
    )
    assert rslt["success"]
    assert len(rslt["outputs"]["test_retry_streams.messages"]) == 4
    assert len(rslt["outputs"]["test_retry_streams.stdouts"]) == 4
    assert len(rslt["outputs"]["test_retry_streams.stderrs"]) == 4
    for i in range(4):
        assert (
            get_s3uri(rslt["outputs"]["test_retry_streams.messages"][i]).decode().strip()
            == "Hello, stdout!"
        )
        assert (
            get_s3uri(rslt["outputs"]["test_retry_streams.stdouts"][i]).decode().strip()
            == "Hello, stdout!"
        )
        assert (
            get_s3uri(rslt["outputs"]["test_retry_streams.stderrs"][i]).decode().strip()
            == "Hello, stderr!"
        )


def test_assemble_refbased(aws_batch, test_s3_folder):
    rslt = batch_miniwdl(
        aws_batch,
        [
            "https://github.com/broadinstitute/viral-pipelines/raw/v2.1.19.0/pipes/WDL/workflows/assemble_refbased.wdl",
            "reads_unmapped_bams=https://github.com/broadinstitute/viral-pipelines/raw/v2.1.19.0/test/input/G5012.3.testreads.bam",
            "reference_fasta=https://github.com/broadinstitute/viral-pipelines/raw/v2.1.19.0/test/input/ebov-makona.fasta",
            "sample_name=G5012.3",
            "--dir",
            "/mnt/efs/miniwdl_aws_tests",
            "--verbose",
        ],
        upload=test_s3_folder + "test_assemble_refbased/",
    )
    assert rslt["success"]
    # TODO: more assertions


def test_termination(aws_batch, test_s3_folder):
    """
    Upon a CommandFailed task failure, the workflow with parallel tasks quickly self-terminates.
    """
    t0 = time.time()
    env = dict(os.environ)
    env["MINIWDL__AWS__CONTAINER_SYNC"] = "true"
    rslt = batch_miniwdl(
        aws_batch,
        [
            "/var/miniwdl_aws_test_assets/test_termination.wdl",
            "--dir",
            "/mnt/efs/miniwdl_aws_tests",
            "--verbose",
        ],
        upload=test_s3_folder + "test_termination/",
        env=env,
    )
    assert not rslt["success"]
    assert rslt["error"]["cause"]["error"] == "CommandFailed"
    assert rslt["error"]["cause"]["exit_status"] == 42
    assert (
        "This is the end, my only friend"
        in get_s3uri(rslt["error"]["cause"]["stderr_s3file"]).decode()
    )
    assert time.time() - t0 < 600


def test_nonexistent_docker(aws_batch, test_s3_folder):
    """
    Workflow specifies a docker image that doesn't exist; does this error bubble up from AWS Batch
    in a reasonable way?
    """
    rslt = batch_miniwdl(
        aws_batch,
        [
            "/var/miniwdl_aws_test_assets/test_nonexistent_docker.wdl",
            "docker=nonexistent_bogus_12345",
            "--dir",
            "/mnt/efs/miniwdl_aws_tests",
            "--delete-after",
            "failure",
            "--verbose",
        ],
        upload=test_s3_folder + "test_nonexistent_docker/",
    )
    assert not rslt["success"]
    assert "CannotPullContainerError" in str(rslt["error"])


def test_call_cache(aws_batch, test_s3_folder):
    """
    Call cache works (short-term, where previous outputs remain on /mnt/shared)
    """
    t0 = int(time.time())
    # run once to prime cache
    rslt = batch_miniwdl(
        aws_batch,
        [
            "/var/miniwdl_aws_test_assets/test_call_cache.wdl",
            "timestamp_in=",
            str(t0),
            "names=Alice",
            "names=Bob",
            "names=Carol",
            "fail=true",
            "--verbose",
            "--dir",
            "/mnt/efs/miniwdl_aws_tests",
        ],
        cache=False,
    )
    assert not rslt["success"]

    # run again where a subset of calls should be reused
    t1 = int(time.time())
    rslt = batch_miniwdl(
        aws_batch,
        [
            "/var/miniwdl_aws_test_assets/test_call_cache.wdl",
            "timestamp_in=",
            str(t0),
            "names=Alice",
            "names=Bob",
            "names=Xavier",
            "--verbose",
            "--dir",
            "/mnt/efs/miniwdl_aws_tests",
        ],
        cache=True,
        upload=test_s3_folder + "test_call_cache/",
    )
    assert rslt["success"]

    # Alice and Bob were cached, Xavier was not:
    assert t0 <= rslt["outputs"]["test_call_cache.timestamps_out"][0] <= t1
    assert t0 <= rslt["outputs"]["test_call_cache.timestamps_out"][1] <= t1
    assert rslt["outputs"]["test_call_cache.timestamps_out"][2] > t1
    assert "Hello, Alice!" in get_s3uri(rslt["outputs"]["test_call_cache.messages"][0]).decode()
    assert "Hello, Bob!" in get_s3uri(rslt["outputs"]["test_call_cache.messages"][1]).decode()
    assert "Hello, Xavier!" in get_s3uri(rslt["outputs"]["test_call_cache.messages"][2]).decode()


def test_call_cache_one_task(aws_batch, test_s3_folder):
    """
    Short-term call cache of one task (where the entire run outputs, not just a portion thereof,
    are sourced from the cache.)
    """
    t0 = int(time.time())
    rslt = batch_miniwdl(
        aws_batch,
        [
            "/var/miniwdl_aws_test_assets/test_call_cache.wdl",
            "timestamp_in=",
            str(t0),
            "name=Alyssa",
            "--task",
            "write_name",
            "--verbose",
            "--dir",
            "/mnt/efs/miniwdl_aws_tests",
        ],
        cache=False,
    )
    assert rslt["success"]

    t1 = int(time.time())
    rslt = batch_miniwdl(
        aws_batch,
        [
            "/var/miniwdl_aws_test_assets/test_call_cache.wdl",
            "timestamp_in=",
            str(t0),
            "name=Alyssa",
            "--task",
            "write_name",
            "--verbose",
            "--dir",
            "/mnt/efs/miniwdl_aws_tests",
        ],
        cache=True,
        upload=test_s3_folder + "test_call_cache_one_task/",
    )
    assert rslt["success"]

    assert t0 <= rslt["outputs"]["write_name.timestamp_out"] <= t1
    assert "Alyssa" in get_s3uri(rslt["outputs"]["write_name.name_file"]).decode()


def test_download(aws_batch):
    """
    Test workflow can use https:// and s3:// input files. This is functionality built-in to miniwdl
    so ought to just work, but nice to cover it here.
    """
    rslt = batch_miniwdl(
        aws_batch,
        [
            "/var/miniwdl_aws_test_assets/count_lines.wdl",
            "files=https://raw.githubusercontent.com/chanzuckerberg/miniwdl/main/tests/alyssa_ben.txt",
            "files=s3://1000genomes/CHANGELOG",
            "--dir",
            "/mnt/efs/miniwdl_aws_tests",
            "--verbose",
        ],
    )
    assert rslt["success"]

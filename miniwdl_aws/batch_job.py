"""
BatchJob: implements miniwdl TaskContainer by submitting jobs to an AWS Batch queue and polling
their status. Assumes a shared filesystem (typically EFS) between the miniwdl host and the Batch
workers.
"""

import os
import math
import time
import json
import threading
import heapq
from contextlib import ExitStack, suppress
import boto3
import botocore
import WDL
import WDL.runtime.task_container
import WDL.runtime._statusbar
from WDL._util import rmtree_atomic, symlink_force, write_atomic
from WDL._util import StructuredLogMessage as _
from ._util import (
    detect_aws_region,
    randomize_job_name,
    efs_id_from_access_point,
    detect_sagemaker_studio_efs,
    detect_studio_fsap,
    detect_gwfcore_batch_queue,
)


class BatchJobBase(WDL.runtime.task_container.TaskContainer):
    """
    Abstract base class implementing the AWS Batch backend for miniwdl TaskContainer. Concrete
    subclasses add configuration specific to the the shared filesystem in use.
    """

    @classmethod
    def global_init(cls, cfg, logger):
        cls._submit_lock = threading.Lock()
        cls._last_submit_time = [0.0]
        cls._init_time = time.time()
        cls._describer = BatchJobDescriber()

        cls._region_name = detect_aws_region(cfg)
        assert (
            cls._region_name
        ), "Failed to detect AWS region; configure AWS CLI or set environment AWS_DEFAULT_REGION"

        # set AWS Batch job queue
        cls._job_queue = cfg.get("aws", "task_queue", "")
        cls._job_queue_fallback = cfg.get("aws", "task_queue_fallback", "")

        # TODO: query Batch compute environment for resource limits
        cls._resource_limits = {"cpu": 9999, "mem_bytes": 999999999999999}

        cls._fs_mount = cfg.get("file_io", "root")
        assert (
            cls._fs_mount.startswith("/") and cls._fs_mount != "/"
        ), "misconfiguration, set [file_io] root / MINIWDL__FILE_IO__ROOT to EFS mount point"

    @classmethod
    def detect_resource_limits(cls, cfg, logger):
        return cls._resource_limits

    def __init__(self, cfg, run_id, host_dir):
        super().__init__(cfg, run_id, host_dir)
        self._logStreamName = None
        self._inputs_copied = False
        # We expect the Batch job containers to have the shared filesystem mounted at the same
        # location we, the workflow job, have it mounted ourselves. Therefore container_dir will be
        # the same as host_dir (unlike the default Swarm backend, which mounts it at a different
        # virtualized location)
        self.container_dir = self.host_dir
        self._aws_interrupts = 0

    def copy_input_files(self, logger):
        self._inputs_copied = True
        return super().copy_input_files(logger)

    def host_work_dir(self):
        # Since we aren't virtualizing the in-container paths as noted above, always use the same
        # working directory on task retries, instead of the base class behavior of appending the
        # try counter (on the host side). This loses some robustness to a split-brain condition
        # where the previous try is actually still running when we start the retry.
        # (see also retry_wait)
        return os.path.join(self.host_dir, "work")

    def host_stdout_txt(self):
        return os.path.join(self.host_dir, "stdout.txt")

    def host_stderr_txt(self):
        return os.path.join(self.host_dir, "stderr.txt")

    def reset(self, logger) -> None:
        cooldown = self.cfg.get_float("aws", "retry_wait", 20.0)
        if cooldown > 0.0:
            logger.info(
                _(
                    "waiting to retry per configuration [aws] retry_wait",
                    seconds=cooldown,
                )
            )
            time.sleep(cooldown)

        rmtree_atomic(self.host_work_dir())
        with suppress(FileNotFoundError):
            os.unlink(self.host_stderr_txt() + ".offset")  # PygtailLogger state file
        super().reset(logger)

    def _run(self, logger, terminating, command):
        """
        Run task
        """
        self._observed_states = set()
        boto3_retries = self.cfg.get_dict(
            "aws", "boto3_retries", {"max_attempts": 5, "mode": "standard"}
        )
        try:
            aws_batch = boto3.Session().client(  # Session() needed for thread safety
                "batch",
                region_name=self._region_name,
                config=botocore.config.Config(retries=boto3_retries),
            )
            with ExitStack() as cleanup:
                # prepare the task working directory
                self._prepare_dir(logger, cleanup, command)
                # submit Batch job (with request throttling)
                job_id = None
                submit_period = self.cfg.get_float("aws", "submit_period", 1.0)
                while True:
                    with self._submit_lock:
                        if terminating():
                            raise WDL.runtime.Terminated(quiet=True)
                        if (
                            time.time() - self._last_submit_time[0]
                            >= submit_period * self._submit_period_multiplier()
                        ):
                            job_id = self._submit_batch_job(logger, cleanup, aws_batch)
                            self._last_submit_time[0] = time.time()
                            break
                    time.sleep(submit_period / 4)
                # poll Batch job status
                return self._await_batch_job(logger, cleanup, aws_batch, job_id, terminating)
        except botocore.exceptions.ClientError as exn:
            wrapper = AWSError(exn)
            logger.error(wrapper)
            raise wrapper

    def _prepare_dir(self, logger, cleanup, command):
        # Prepare control files. We do NOT use super().touch_mount_point(...) because it fails if
        # the desired mount point already exists; which it may in our case after a retry (see
        # self.host_work_dir() override above.)
        with open(os.path.join(self.host_dir, "command"), "w") as outfile:
            outfile.write(command)
        with open(self.host_stdout_txt(), "w"):
            pass
        with open(self.host_stderr_txt(), "w"):
            pass

        if not self._inputs_copied:
            # Prepare symlinks to the input Files & Directories
            container_prefix = os.path.join(self.container_dir, "work/_miniwdl_inputs/")
            link_dirs_made = set()
            for host_fn, container_fn in self.input_path_map.items():
                assert container_fn.startswith(container_prefix) and len(container_fn) > len(
                    container_prefix
                )
                if host_fn.endswith("/"):
                    assert container_fn.endswith("/")
                    host_fn = host_fn[:-1]
                    container_fn = container_fn[:-1]
                else:
                    assert not container_fn.endswith("/")
                link_dn = os.path.dirname(container_fn)
                if link_dn not in link_dirs_made:
                    os.makedirs(link_dn)
                    link_dirs_made.add(link_dn)
                symlink_force(host_fn, container_fn)

    def _submit_batch_job(self, logger, cleanup, aws_batch):
        """
        Register & submit AWS batch job, leaving a cleanup callback to deregister the transient
        job definition.
        """

        job_name = self.run_id
        if job_name.startswith("call-"):
            job_name = job_name[5:]
        if self.try_counter > 1:
            job_name += f"-try{self.try_counter}"
        # Append entropy to the job name to avoid race condition using identical job names in
        # concurrent RegisterJobDefinition requests
        job_name = randomize_job_name(job_name)

        container_properties = self._prepare_container_properties(logger)
        job_def = aws_batch.register_job_definition(
            jobDefinitionName=job_name,
            type="container",
            containerProperties=container_properties,
        )
        job_def_handle = f"{job_def['jobDefinitionName']}:{job_def['revision']}"
        logger.debug(
            _(
                "registered Batch job definition",
                jobDefinition=job_def_handle,
                **container_properties,
            )
        )

        self._cleanup_job_definition(logger, cleanup, aws_batch, job_def_handle)

        job_queue = self._select_job_queue()
        job_tags = self.cfg.get_dict("aws", "job_tags", {})
        if "AWS_BATCH_JOB_ID" in os.environ:
            # If we find ourselves running inside an AWS Batch job, tag the new job identifying
            # ourself as the "parent" job.
            job_tags["AWS_BATCH_PARENT_JOB_ID"] = os.environ["AWS_BATCH_JOB_ID"]
        # TODO: set a tag to indicate that this job is a retry of another
        job = aws_batch.submit_job(
            jobName=job_name,
            jobQueue=job_queue,
            jobDefinition=job_def_handle,
            timeout={"attemptDurationSeconds": self.cfg.get_int("aws", "job_timeout", 86400)},
            tags=job_tags,
        )
        logger.info(
            _(
                "AWS Batch job submitted",
                jobQueue=job_queue,
                jobId=job["jobId"],
                tags=job_tags,
            )
        )
        return job["jobId"]

    def _select_job_queue(self):
        if self._job_queue_fallback:
            preemptible = self.runtime_values.get("preemptible", 0)
            if self._aws_interrupts >= preemptible and preemptible > 0:
                return self._job_queue_fallback
        return self._job_queue

    def _prepare_container_properties(self, logger):
        image_tag = self.runtime_values.get("docker", "ubuntu:20.04")
        vcpu = self.runtime_values.get("cpu", 1)
        memory_mbytes = max(
            math.ceil(self.runtime_values.get("memory_reservation", 0) / 1048576), 1024
        )
        commands = [
            f"cd {self.container_dir}/work",
            "exit_code=0",
            self.cfg.get("task_runtime", "command_shell")
            + " ../command >> ../stdout.txt 2> >(tee -a ../stderr.txt >&2) || exit_code=$?",
        ]
        if self.cfg.get_bool("aws", "container_sync", False):
            commands.append("find . -type f | xargs sync")
            commands.append("sync ../stdout.txt ../stderr.txt")
        commands.append("exit $exit_code")

        resource_requirements = [
            {"type": "VCPU", "value": str(vcpu)},
            {"type": "MEMORY", "value": str(memory_mbytes)},
        ]

        if self.runtime_values.get("gpu", False):
            resource_requirements += [{"type": "GPU", "value": "1"}]

        container_properties = {
            "image": image_tag,
            "command": ["/bin/bash", "-ec", "\n".join(commands)],
            "environment": [
                {"name": ev_name, "value": ev_value}
                for ev_name, ev_value in self.runtime_values.get("env", dict())
            ],
            "resourceRequirements": resource_requirements,
            "privileged": self.runtime_values.get("privileged", False),
            "mountPoints": [{"containerPath": self._fs_mount, "sourceVolume": "file_io_root"}],
        }

        if self.cfg["task_runtime"].get_bool("as_user"):
            user = f"{os.geteuid()}:{os.getegid()}"
            if user.startswith("0:"):
                logger.warning(
                    "container command will run explicitly as root, since you are root and set --as-me"
                )
            container_properties["user"] = user

        return container_properties

    def _cleanup_job_definition(self, logger, cleanup, aws_batch, job_def_handle):
        def deregister(logger, aws_batch, job_def_handle):
            try:
                aws_batch.deregister_job_definition(jobDefinition=job_def_handle)
                logger.debug(_("deregistered Batch job definition", jobDefinition=job_def_handle))
            except botocore.exceptions.ClientError as exn:
                # AWS expires job definitions after 6mo, so failing to delete them isn't fatal
                logger.warning(
                    _(
                        "failed to deregister Batch job definition",
                        jobDefinition=job_def_handle,
                        error=str(AWSError(exn)),
                    )
                )

        cleanup.callback(deregister, logger, aws_batch, job_def_handle)

    def _await_batch_job(self, logger, cleanup, aws_batch, job_id, terminating):
        """
        Poll for Batch job success or failure & return exit code
        """
        describe_period = self.cfg.get_float("aws", "describe_period", 1.0)
        cleanup.callback((lambda job_id: self._describer.unsubscribe(job_id)), job_id)
        poll_stderr = cleanup.enter_context(self.poll_stderr_context(logger))
        exit_code = None
        while exit_code is None:
            time.sleep(describe_period)
            job_desc = self._describer.describe(aws_batch, job_id, describe_period)
            write_atomic(
                json.dumps(job_desc, indent=2, sort_keys=True),
                os.path.join(self.host_dir, f"awsBatchJobDetail.{job_id}.json"),
            )
            job_status = job_desc["status"]
            if "container" in job_desc and "logStreamName" in job_desc["container"]:
                self._logStreamName = job_desc["container"]["logStreamName"]
            if job_status not in self._observed_states:
                self._observed_states.add(job_status)
                logfn = (
                    logger.notice
                    if job_status in ("RUNNING", "SUCCEEDED", "FAILED")
                    else logger.info
                )
                logdetails = {"status": job_status, "jobId": job_id}
                if self._logStreamName:
                    logdetails["logStreamName"] = self._logStreamName
                logfn(_("AWS Batch job change", **logdetails))
                if job_status == "STARTING" or (
                    job_status == "RUNNING" and "STARTING" not in self._observed_states
                ):
                    cleanup.enter_context(self.task_running_context())
                if job_status not in (
                    "SUBMITTED",
                    "PENDING",
                    "RUNNABLE",
                    "STARTING",
                    "RUNNING",
                    "SUCCEEDED",
                    "FAILED",
                ):
                    logger.warning(_("unknown job status from AWS Batch", status=job_status))
            if job_status == "SUCCEEDED":
                exit_code = 0
            elif job_status == "FAILED":
                reason = job_desc.get("container", {}).get("reason", None)
                status_reason = job_desc.get("statusReason", None)
                self.failure_info = {"jobId": job_id}
                if reason:
                    self.failure_info["reason"] = reason
                if status_reason:
                    self.failure_info["statusReason"] = status_reason
                if self._logStreamName:
                    self.failure_info["logStreamName"] = self._logStreamName
                if status_reason and "Host EC2" in status_reason and "terminated" in status_reason:
                    self._aws_interrupts += 1
                    raise WDL.runtime.Interrupted(
                        "AWS Batch job interrupted (likely spot instance termination)",
                        more_info=self.failure_info,
                    )
                if "exitCode" not in job_desc.get("container", {}):
                    raise WDL.Error.RuntimeError(
                        "AWS Batch job failed", more_info=self.failure_info
                    )
                exit_code = job_desc["container"]["exitCode"]
                assert isinstance(exit_code, int) and exit_code != 0
            if "RUNNING" in self._observed_states:
                poll_stderr()
            if terminating():
                aws_batch.terminate_job(jobId=job_id, reason="terminated by miniwdl")
                raise WDL.runtime.Terminated(
                    quiet=not self._observed_states.difference({"SUBMITTED", "PENDING", "RUNNABLE"})
                )
        for _root, _dirs, _files in os.walk(self.host_dir, followlinks=False):
            # no-op traversal of working directory to refresh NFS metadata cache (speculative)
            pass
        poll_stderr()
        return exit_code

    def _submit_period_multiplier(self):
        if self._describer.jobs:
            b = self.cfg.get_float("aws", "submit_period_b", 0.0)
            if b > 0.0:
                t = time.time() - self._init_time
                c = self.cfg.get_float("aws", "submit_period_c", 0.0)
                return max(1.0, c - t / b)
        return 1.0


class BatchJob(BatchJobBase):
    """
    EFS-based implementation, including the case of SageMaker Studio's built-in EFS. Assumes we're
    running on an EC2 instance or Fargate container mounting an EFS Access Point at [file_io] root,
    and configures each Batch job with the same mount.
    """

    @classmethod
    def global_init(cls, cfg, logger):
        super().global_init(cfg, logger)

        # EFS configuration based on:
        # - [aws] fsap / MINIWDL__AWS__FSAP
        # - [aws] fs / MINIWDL__AWS__FS
        # - SageMaker Studio metadata, if applicable
        cls._fs_id = None
        cls._fsap_id = None
        if cfg.has_option("aws", "fs"):
            cls._fs_id = cfg.get("aws", "fs")
        if cfg.has_option("aws", "fsap"):
            cls._fsap_id = cfg.get("aws", "fsap")
            if not cls._fs_id:
                cls._fs_id = efs_id_from_access_point(cls._region_name, cls._fsap_id)
        cls._studio_efs_uid = None
        sagemaker_studio_efs = detect_sagemaker_studio_efs(logger, region_name=cls._region_name)
        if sagemaker_studio_efs:
            (
                studio_efs_id,
                studio_efs_uid,
                studio_efs_home,
                studio_efs_mount,
            ) = sagemaker_studio_efs
            assert (
                not cls._fs_id or cls._fs_id == studio_efs_id
            ), "Configured EFS ([aws] fs / MINIWDL__AWS__FS, [aws] fsap / MINIWDL__AWS__FSAP) isn't associated with current SageMaker Studio domain EFS"
            cls._fs_id = studio_efs_id
            assert (
                cls._fs_mount.rstrip("/") == studio_efs_mount.rstrip("/")
            ) or cls._fs_mount.startswith(studio_efs_mount.rstrip("/") + "/"), (
                "misconfiguration, set [file_io] root / MINIWDL__FILE_IO__ROOT to "
                + studio_efs_mount.rstrip("/")
            )
            cls._studio_efs_uid = studio_efs_uid
            if not cls._fsap_id:
                cls._fsap_id = detect_studio_fsap(
                    logger,
                    studio_efs_id,
                    studio_efs_uid,
                    studio_efs_home,
                    region_name=cls._region_name,
                )
                assert (
                    cls._fsap_id
                ), "Unable to detect suitable EFS Access Point for use with SageMaker Studio; set [aws] fsap / MINIWDL__AWS__FSAP"
            # TODO: else sanity-check that FSAP's root directory equals studio_efs_home
        assert (
            cls._fs_id
        ), "Missing EFS configuration ([aws] fs / MINIWDL__AWS__FS or [aws] fsap / MINIWDL__AWS__FSAP)"
        if not cls._fsap_id:
            logger.warning(
                "AWS BatchJob plugin recommends using EFS Access Point to simplify permissions between containers (configure [aws] fsap / MINIWDL__AWS__FSAP to fsap-xxxx)"
            )

        # if no task queue in config file, try detecting miniwdl-aws-studio
        if not cls._job_queue and sagemaker_studio_efs:
            cls._job_queue = detect_gwfcore_batch_queue(
                logger, sagemaker_studio_efs[0], region_name=cls._region_name
            )
        assert (
            cls._job_queue
        ), "Missing AWS Batch job queue configuration ([aws] task_queue / MINIWDL__AWS__TASK_QUEUE)"

        logger.info(
            _(
                "initialized AWS BatchJob (EFS) plugin",
                region_name=cls._region_name,
                job_queue=cls._job_queue,
                resource_limits=cls._resource_limits,
                file_io_root=cls._fs_mount,
                efs_id=cls._fs_id,
                efsap_id=cls._fsap_id,
            )
        )

    def _prepare_container_properties(self, logger):
        container_properties = super()._prepare_container_properties(logger)

        # add EFS volume & mount point
        volumes = [
            {
                "name": "file_io_root",
                "efsVolumeConfiguration": {
                    "fileSystemId": self._fs_id,
                    "transitEncryption": "ENABLED",
                },
            }
        ]
        if self._fsap_id:
            volumes[0]["efsVolumeConfiguration"]["authorizationConfig"] = {
                "accessPointId": self._fsap_id
            }
        container_properties["volumes"] = volumes

        # set Studio UID if appropriate
        if self.cfg["task_runtime"].get_bool("as_user") and self._studio_efs_uid:
            container_properties["user"] = f"{self._studio_efs_uid}:{self._studio_efs_uid}"

        return container_properties


class BatchJobNoEFS(BatchJobBase):
    """
    Implementation assuming the Batch compute environment is configured to mount the shared
    filesystem without further specification by us; e.g. FSxL mounted by cloud-init user data
    script.
    """

    @classmethod
    def global_init(cls, cfg, logger):
        super().global_init(cfg, logger)

        assert (
            cls._job_queue
        ), "Missing AWS Batch job queue configuration ([aws] task_queue / MINIWDL__AWS__TASK_QUEUE)"

        logger.info(
            _(
                "initialized AWS BatchJob plugin",
                region_name=cls._region_name,
                job_queue=cls._job_queue,
                resource_limits=cls._resource_limits,
                file_io_root=cls._fs_mount,
            )
        )

    def _prepare_container_properties(self, logger):
        container_properties = super()._prepare_container_properties(logger)

        container_properties["volumes"] = [
            {
                "name": "file_io_root",
                "host": {"sourcePath": self._fs_mount},
            }
        ]

        return container_properties


class BatchJobDescriber:
    """
    This singleton object handles calling the AWS Batch DescribeJobs API with up to 100 job IDs
    per request, then dispensing each job description to the thread interested in it. This helps
    avoid AWS API request rate limits when we're tracking many concurrent jobs.
    """

    JOBS_PER_REQUEST = 100  # maximum jobs per DescribeJob request

    def __init__(self):
        self.lock = threading.Lock()
        self.last_request_time = 0
        self.job_queue = []
        self.jobs = {}

    def describe(self, aws_batch, job_id, period):
        """
        Get the latest Batch job description
        """
        while True:
            with self.lock:
                if job_id not in self.jobs:
                    # register new job to be described ASAP
                    heapq.heappush(self.job_queue, (0.0, job_id))
                    self.jobs[job_id] = None
                # update as many job descriptions as possible
                self._update(aws_batch, period)
                # return the desired job description if we have it
                desc = self.jobs[job_id]
                if desc:
                    return desc
            # otherwise wait (outside the lock) and try again
            time.sleep(period / 4)

    def unsubscribe(self, job_id):
        """
        Unsubscribe from a job_id once we'll no longer be interested in it
        """
        with self.lock:
            if job_id in self.jobs:
                del self.jobs[job_id]

    def _update(self, aws_batch, period):
        # if enough time has passed since our last DescribeJobs request
        if time.time() - self.last_request_time >= period:
            # take the N least-recently described jobs
            job_ids = set()
            assert self.job_queue
            while self.job_queue and len(job_ids) < self.JOBS_PER_REQUEST:
                job_id = heapq.heappop(self.job_queue)[1]
                assert job_id not in job_ids
                if job_id in self.jobs:
                    job_ids.add(job_id)
            if not job_ids:
                return
            # describe them
            try:
                job_descs = aws_batch.describe_jobs(jobs=list(job_ids))
            finally:
                # always: bump last_request_time and re-enqueue these jobs
                self.last_request_time = time.time()
                for job_id in job_ids:
                    heapq.heappush(self.job_queue, (self.last_request_time, job_id))
            # update self.jobs with the new descriptions
            for job_desc in job_descs["jobs"]:
                job_ids.remove(job_desc["jobId"])
                self.jobs[job_desc["jobId"]] = job_desc
            assert not job_ids, "AWS Batch DescribeJobs didn't return all expected results"


class AWSError(WDL.Error.RuntimeError):
    """
    Repackage botocore.exceptions.ClientError to surface it more-informatively in miniwdl task log
    """

    def __init__(self, client_error: botocore.exceptions.ClientError):
        assert isinstance(client_error, botocore.exceptions.ClientError)
        msg = (
            f"{client_error.response['Error']['Code']}, {client_error.response['Error']['Message']}"
        )
        super().__init__(
            msg, more_info={"ResponseMetadata": client_error.response["ResponseMetadata"]}
        )

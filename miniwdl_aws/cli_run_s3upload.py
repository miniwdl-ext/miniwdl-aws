"""
miniwdl_run_s3upload CLI entry point (console script) which passes through its arguments to
`miniwdl run`, then uploads run artifacts to $S3_UPLOAD_FOLDER. This includes the log file and if
the run succeeded, the output files and outputs.json (rewritten with the uploaded S3 URIs instead
of local filenames).

With the BatchJob plugin also enabled, this may be used from an SSH session on an EC2 instance or
container with EFS suitably mounted at /mnt/efs; or within a Batch "workflow job."
"""

import sys
import os
import json
import subprocess
import shutil
import argparse
import tempfile
import signal
from ._util import END_OF_LOG, subprocess_run_with_clean_exit


def miniwdl_run_s3upload():
    # Set signal handler. SystemExit may be handled below and/or by subprocess_run_with_clean_exit.
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGQUIT):
        signal.signal(s, lambda sig, _: (_ for _ in ()).throw(SystemExit(sig)))

    # run main logic with handlers
    try:
        end_log_and_exit(miniwdl_run_s3upload_inner())
    except SystemExit as exc:
        end_log_and_exit(exc.code)
    except KeyboardInterrupt:
        end_log_and_exit(int(signal.SIGINT))
    except BrokenPipeError:
        end_log_and_exit(int(signal.SIGPIPE))


def end_log_and_exit(code):
    print(
        "\n" + END_OF_LOG,
        file=sys.stderr,
    )
    sys.exit(code)


def miniwdl_run_s3upload_inner():
    parser = argparse.ArgumentParser(
        prog="miniwdl-run-s3upload",
        description="Pass through arguments to `miniwdl run` and afterwards, upload outputs to S3 and optionally delete local run directory.",
        usage="miniwdl-run-s3upload [miniwdl_run_arg ...]",
        allow_abbrev=False,
    )
    parser.add_argument(
        "--s3upload",
        help="s3://bucket/folder/ at which to upload run outputs [env MINIWDL__AWS__S3_UPLOAD_FOLDER]",
    )
    parser.add_argument(
        "--delete-after",
        choices=("always", "success", "failure"),
        help="with --s3upload, delete EFS run directory afterwards [env MINIWDL__AWS__S3_UPLOAD_DELETE_AFTER]",
    )
    parser.add_argument(
        "--task-queue", help="AWS Batch job queue for task jobs [env MINIWDL__AWS__TASK_QUEUE]"
    )

    args, unused_args = parser.parse_known_args(sys.argv[1:])
    args.s3upload = (
        args.s3upload if args.s3upload else os.environ.get("MINIWDL__AWS__S3_UPLOAD_FOLDER", None)
    )
    args.delete_after = (
        args.delete_after.strip().lower()
        if args.delete_after
        else os.environ.get("MINIWDL__AWS__S3_UPLOAD_DELETE_AFTER", None)
    )
    if args.delete_after and not args.s3upload:
        print("--delete-after requires --s3upload", file=sys.stderr)
        sys.exit(1)

    if args.s3upload:
        with tempfile.TemporaryDirectory() as tmpdir:
            testfile = os.path.join(tmpdir, ".test.miniwdl-run-s3upload")
            with open(testfile, "w") as outfile:
                print(
                    "miniwdl-run-s3upload created this object to test bucket permissions.",
                    file=outfile,
                )
            upload1(testfile, args.s3upload + ("/" if not args.s3upload.endswith("/") else ""))

    cmd = ["miniwdl", "run"] + unused_args
    if "--error-json" not in unused_args:
        cmd.append("--error-json")
    miniwdl_env = dict(os.environ)
    if args.task_queue:  # pass through to BatchJob plugin via env var
        miniwdl_env["MINIWDL__AWS__TASK_QUEUE"] = args.task_queue

    # run miniwdl & tee its standard output
    miniwdl = subprocess_run_with_clean_exit(
        cmd, stdout=subprocess.PIPE, env=miniwdl_env, check=False
    )
    sys.stdout.buffer.write(miniwdl.stdout)

    if not args.s3upload:
        # nothing to do
        print(
            f"[miniwdl_run_s3upload] no setting for --s3upload / MINIWDL__AWS__S3_UPLOAD_FOLDER; exiting (code = {miniwdl.returncode})",
            file=sys.stderr,
        )
        return miniwdl.returncode

    # read miniwdl standard output JSON
    try:
        miniwdl_json = json.loads(miniwdl.stdout)
        run_dir = miniwdl_json["dir"]
        assert os.path.isdir(run_dir)
    except:
        print(
            f"[miniwdl_run_s3upload] no run directory in miniwdl standard output; exiting (code = {miniwdl.returncode})",
            file=sys.stderr,
        )
        return miniwdl.returncode

    # append miniwdl's run name to S3_UPLOAD_FOLDER (unless the latter ends in '/')
    s3_upload_folder = args.s3upload
    if not s3_upload_folder.endswith("/"):
        s3_upload_folder += os.path.basename(run_dir.rstrip("/")) + "/"

    # upload logs
    print(
        f"[miniwdl_run_s3upload] miniwdl exit code = {miniwdl.returncode}; uploading logs & outputs to {s3_upload_folder}",
        file=sys.stderr,
    )
    for p in (os.path.join(run_dir, fn) for fn in ("workflow.log", "task.log")):
        if os.path.isfile(p):
            upload1(p, s3_upload_folder)

    # upload error.json, and the stderr_file it points to, if any
    error_json_file = os.path.join(run_dir, "error.json")
    if os.path.isfile(error_json_file):
        upload1(error_json_file, s3_upload_folder)
        with open(error_json_file) as infile:
            error_json = json.load(infile)
            stderr_file = error_json.get("cause", {}).get("stderr_file", None)
            if stderr_file and os.path.isfile(stderr_file):
                stderr_s3file = s3_upload_folder + "CommandFailed_stderr.txt"
                upload1(stderr_file, stderr_s3file)
                error_json["cause"]["stderr_s3file"] = stderr_s3file
                with tempfile.NamedTemporaryFile() as tmp:
                    tmp.write(json.dumps(error_json, indent=2).encode())
                    tmp.flush()
                    upload1(tmp.name, s3_upload_folder + "error.json")

    # upload output files, if any
    if os.path.isdir(os.path.join(run_dir, "out")):
        subprocess_run_with_clean_exit(
            [
                "aws",
                "s3",
                "sync",
                "--no-progress",
                "--follow-symlinks",
                os.path.join(run_dir, "out"),
                s3_upload_folder,
            ],
            check=True,
        )

    if "outputs" not in miniwdl_json:
        if args.delete_after in ("always", "failure"):
            shutil.rmtree(run_dir)
            print(
                f"[miniwdl_run_s3upload] deleted {run_dir}",
                file=sys.stderr,
            )
        return miniwdl.returncode

    # recursively rewrite outputs JSON
    def rewrite(v):

        if v and isinstance(v, str) and v[0] == "/" and os.path.exists(v):
            # miniwdl writes File/Directory outputs with absolute paths
            return rebase_output_path(v, run_dir, s3_upload_folder)
        if isinstance(v, list):
            return [rewrite(u) for u in v]
        if isinstance(v, dict):
            return dict((k, rewrite(u)) for (k, u) in v.items())
        return v

    rewritten_outputs = rewrite(miniwdl_json["outputs"])
    outputs_s3_json = os.path.join(run_dir, "outputs.s3.json")
    with open(outputs_s3_json + ".tmp", "w") as outfile:
        print(json.dumps(rewritten_outputs, indent=2), file=outfile)
    os.rename(outputs_s3_json + ".tmp", outputs_s3_json)
    upload1(outputs_s3_json, s3_upload_folder + "outputs.json")
    print(
        f"[miniwdl_run_s3upload] uploaded {s3_upload_folder}outputs.json",
        file=sys.stderr,
    )
    print(json.dumps({"s3upload": s3_upload_folder, "outputs": rewritten_outputs}, indent=2))
    if args.delete_after in ("always", "success"):
        shutil.rmtree(run_dir)
        print(
            f"[miniwdl_run_s3upload] deleted {run_dir}",
            file=sys.stderr,
        )

    return miniwdl.returncode


def upload1(fn, dest):
    subprocess_run_with_clean_exit(["aws", "s3", "cp", "--no-progress", fn, dest], check=True)


def rebase_output_path(fn, run_dir, s3_upload_folder):
    """
    Given extant filename `fn` from JSON outputs and the current run directory, figure the uploaded
    S3 URI under s3_upload_folder, where the file should be uploaded by our `aws s3 sync` operation
    on the "run out" directory. Or return fn unmodified if it seems to be something that looks like
    an output path, but isn't really.

    Subtlety: if the output fn originated from the call cache, it will be from some other run
    directory, not the current one. In that case we need to see that there's a corresponding link
    under the current run out directory.

    There should be no danger of inadvertently uploading non-output files (e.g. if the workflow
    outputs the string "/home/root/.ssh/id_rsa") because we're not actually performing the upload,
    just figuring the path where `aws s3 sync` ought to have uploaded it.
    """
    fn_parts = fn.strip("/").split("/")
    while fn_parts:
        fn_rel = "/".join(fn_parts)
        fn_rebased = os.path.join(run_dir, "out", fn_rel)
        if os.path.exists(fn_rebased) and os.path.isdir(fn) == os.path.isdir(fn_rebased):
            return s3_upload_folder + fn_rel
        fn_parts = fn_parts[1:]
    return fn

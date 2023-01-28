from setuptools import setup, find_packages
from version import get_version

with open("README.md") as fp:
    long_description = fp.read()

setup(
    name="miniwdl-aws",
    version=get_version(),
    description="miniwdl AWS backend (Batch+EFS)",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Wid L. Hacker",
    python_requires=">=3.6",
    packages=find_packages(),
    setup_requires=["reentry"],
    install_requires=["miniwdl>=1.9.0", "boto3>=1.17", "requests"],
    reentry_register=True,
    entry_points={
        "miniwdl.plugin.container_backend": [
            "aws_batch_job = miniwdl_aws:BatchJob",
            "aws_batch_job_no_efs = miniwdl_aws:BatchJobNoEFS",
        ],
        "console_scripts": [
            "miniwdl-run-s3upload = miniwdl_aws:miniwdl_run_s3upload",
            "miniwdl-aws-submit = miniwdl_aws.__main__:main",
        ],
    },
)

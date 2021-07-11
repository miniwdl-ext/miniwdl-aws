from setuptools import setup, find_packages

setup(
    name="miniwdl-aws",
    version="0.1.0",  # TODO: detect git tag
    description="miniwdl AWS backend (Batch+EFS)",
    author="Wid L. Hacker",
    python_requires=">=3.6",
    packages=find_packages(),
    setup_requires=["reentry"],
    install_requires=["miniwdl>=1.2.1", "boto3>=1.17", "requests"],
    reentry_register=True,
    entry_points={
        "miniwdl.plugin.container_backend": ["aws_batch_job = miniwdl_aws:BatchJob"],
        "console_scripts": [
            "miniwdl-run-s3upload = miniwdl_aws:miniwdl_run_s3upload",
            "miniwdl-aws-submit = miniwdl_aws.__main__:main",
        ],
    },
)

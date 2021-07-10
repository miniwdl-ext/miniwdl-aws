from setuptools import setup, find_packages

setup(
    name="miniwdl_plugin_aws",
    version="0.0.1",
    description="miniwdl AWS backend (Batch+EFS)",
    author="Wid L. Hacker",
    python_requires=">=3.6",
    packages=find_packages(),
    setup_requires=["reentry"],
    install_requires=["miniwdl>=1.2.1", "boto3>=1.17", "requests"],
    reentry_register=True,
    entry_points={
        "miniwdl.plugin.container_backend": ["aws_batch_job = miniwdl_plugin_aws:BatchJob"],
        "console_scripts": [
            "miniwdl_run_s3upload = miniwdl_plugin_aws:miniwdl_run_s3upload",
            "miniwdl_submit_awsbatch = miniwdl_plugin_aws.__main__:main",
        ],
    },
)

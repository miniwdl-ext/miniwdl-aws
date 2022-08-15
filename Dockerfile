# Docker image with miniwdl & the AWS plugin baked in. Suitable for submission to Batch as the
# "workflow job" launching & monitoring other jobs (WDL tasks).

FROM public.ecr.aws/amazonlinux/amazonlinux:2

# rpm dependencies
RUN yum check-update; yum install -y \
        python3-pip \
        awscli \
        tar

# miniwdl-aws (and PyPI dependencies listed in setup.py)
COPY ./ /tmp/miniwdl-aws/
RUN bash -c 'cd /tmp/miniwdl-aws && pip3 install .'

# cleanup (for squashed image)
RUN yum clean all && rm -rf /tmp/miniwdl*

# boilerplate configuration file & test assets
COPY miniwdl_aws.cfg /etc/xdg/miniwdl.cfg
COPY test/assets/ /var/miniwdl_aws_test_assets/

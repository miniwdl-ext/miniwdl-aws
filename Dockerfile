# Docker image with miniwdl & the AWS plugin baked in. Suitable for submission to Batch as the
# "workflow job" launching & monitoring other jobs (WDL tasks).

FROM public.ecr.aws/amazonlinux/amazonlinux:2023

# rpm dependencies
RUN yum check-update; yum install -y \
        python3-pip \
        python3-setuptools \
        unzip

# AWS CLI v2 (`yum install awscli` is a really old version)
RUN curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip"
RUN sh -c 'cd /tmp && unzip awscliv2.zip' && sh /tmp/aws/install

# miniwdl-aws (and PyPI dependencies listed in setup.py)
COPY ./ /tmp/miniwdl-aws/
RUN bash -c 'cd /tmp/miniwdl-aws && pip3 install .'

# cleanup (for squashed image)
RUN yum clean all && rm -rf /tmp/miniwdl* /tmp/aws*

# boilerplate configuration file & test assets
COPY miniwdl_aws.cfg /etc/xdg/miniwdl.cfg
COPY test/assets/ /var/miniwdl_aws_test_assets/

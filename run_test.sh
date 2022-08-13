export MINIWDL__AWS__TASK_QUEUE=miniwdl-task
export MINIWDL__AWS__FSAP=fsap-03864f63f6060096d
export MINIWDL__AWS__FS=fs-0fc11fd546f3df122
export MINIWDL__FILE_IO__ROOT=/mnt/efs

env | grep MINIWDL
#miniwdl-run-s3upload --verbose --no-cache --dir /mnt/efs/miniwdl_aws_tests test_workflow/self_test/test.wdl who=https://raw.githubusercontent.com/chanzuckerberg/miniwdl/main/tests/alyssa_ben.txt
miniwdl-run-s3upload --verbose --no-cache --dir /mnt/efs/miniwdl_aws_tests test_workflow/gpu_test/gpu_test.wdl

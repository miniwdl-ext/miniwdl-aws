version 1.0
# This WDL tests stdout/stderr outputs and automatic task retry. The task outputs messages via
# captured standard output and error files, but fails on 3/4 attempts. We're looking for the tasks
# to ultimately succeed and to produce the expected outputs (in particular, each output file should
# have only one message, despite the task potentially having been tried multiple times).

workflow test_retry_streams {
    input {}

    scatter (i in range(4)) {
        call test_retry_streams_task
    }

    output {
        Array[File] messages = test_retry_streams_task.message
        Array[File] stdouts = test_retry_streams_task.stdout
        Array[File] stderrs = test_retry_streams_task.stderr
    }
}

task test_retry_streams_task {
    input {}

    command <<<
        echo "Hello, stdout!" | tee message.txt
        >&2 echo "Hello, stderr!"
        if (( RANDOM % 4 > 0)); then
            exit 42
        fi
    >>>

    output {
        File message = "message.txt"
        File stdout = stdout()
        File stderr = stderr()
    }

    runtime {
        docker: "ubuntu:20.04"
        cpu: 1
        maxRetries: 99
    }
}

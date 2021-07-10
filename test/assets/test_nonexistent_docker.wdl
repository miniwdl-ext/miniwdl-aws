version 1.1

task t {
    input {
        String docker
    }
    command {
        echo "Hello, world!"
    }
    runtime {
        docker: docker
    }
}

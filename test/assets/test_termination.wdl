version 1.1

workflow w {
    scatter (i in range(4)) {
        call t {
            input:
            i
        }
    }
}

task t {
    input {
        Int i
    }

    command <<<
        if (( ~{i} == 3 )); then
            sleep 10
            >&2 echo "This is the end, my only friend"
            exit 42
        fi
        sleep 600
    >>>
}

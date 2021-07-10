version 1.1

workflow test_call_cache {
    input {
        Array[String] names
        Int timestamp_in  # set to test case start time, preventing use of stale cache test entries
        Boolean fail = false
    }
    scatter (name in names) {
        call write_name {
            input:
            name = name,
            timestamp_in = timestamp_in
        }
        call t {
            input:
            who = write_name.name_file,
            timestamp_in = timestamp_in
        }
    }
    if (fail) {
        call failer after t
    }
    output {
        Array[Int] timestamps_out = t.timestamp_out
        Array[File] messages = t.message
    }
}

task write_name {
    input {
        String name
        Int timestamp_in
    }
    command {
        cp '~{write_lines([name])}' name.txt
    }
    output {
        File name_file = "name.txt"
        Int timestamp_out = timestamp_in
    }
}

task t {
    input {
        File who
        Int timestamp_in
    }
    command <<<
        t=$(date +%s)
        echo "$t" > timestamp_out
        echo "Hello, $(cat ~{who})! @$t" | tee message.txt
    >>>
    output {
        Int timestamp_out = read_int("timestamp_out")
        File message = "message.txt"
    }
}

task failer {
    command {
        exit 1
    }
}

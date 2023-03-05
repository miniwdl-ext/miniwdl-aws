version development

workflow test_directory_workflow {
    input {
        Array[String] names = ["Alice", "Bob", "Carol"]
    }
    call make_directory {
        input:
        names
    }
    call test_directory {
        input:
        dir = make_directory.dir
    }
    output {
        Directory dir = make_directory.dir
        File report = test_directory.report
        Int file_count = test_directory.file_count
    }
}

task make_directory {
    input {
        Array[String] names
    }

    File names_file = write_lines(names)

    command <<<
        mkdir messages
        while read -r name; do
            echo "Hello, $name!" > "messages/$name.txt"
        done < '~{names_file}'
    >>>

    output {
        Directory dir = "messages"
    }
}

task test_directory {
    input {
        Directory dir
    }

    command <<<
        find '~{dir}' -type f | xargs sha256sum > report.txt
        find '~{dir}' -type f | wc -l > file.count
    >>>

    output {
        File report = "report.txt"
        Int file_count = read_int("file.count")
    }

    runtime {
        docker: "ubuntu:22.04"
    }
}

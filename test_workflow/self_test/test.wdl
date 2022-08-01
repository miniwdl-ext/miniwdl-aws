            version 1.0
            workflow hello_caller {
                input {
                    File who
                }
                scatter (name in read_lines(who)) {
                    call hello {
                        input:
                            who = write_lines([name])
                    }
                    if (defined(hello.message)) {
                        String msg = read_string(select_first([hello.message]))
                    }
                }
                output {
                    Array[String] messages = select_all(msg)
                }
            }
            task hello {
                input {
                    File who
                }
                command {
                    if grep -qv ^\# "${who}" ; then
                        echo "Hello, $(cat ${who})!" | tee message.txt 1>&2
                    fi
                }
                output {
                    File? message = "message.txt"
                }
                runtime {
                    docker: "ubuntu:18.04"
                    memory: "1G"
                }
            }
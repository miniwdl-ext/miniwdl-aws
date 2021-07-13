version 1.0
workflow count_lines {
    input {
        Array[File] files
    }
    scatter (file in files) {
        Array[String] file_lines = read_lines(file)
    }
    output {
        Int lines = length(flatten(file_lines))
    }
}

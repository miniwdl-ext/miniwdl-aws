version 1.0

workflow test {
  call nvidia_test
}

task nvidia_test {

  command {
    nvidia-smi
  }

  output {
    File response = stdout()
  }

  runtime {
   docker: 'nvidia/cuda:11.4.0-base-ubuntu20.04'
   gpu:true
  }
}


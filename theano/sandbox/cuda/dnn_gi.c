#section support_code_struct

int
APPLY_SPECIFIC(conv_gi)(CudaNdarray *kerns, CudaNdarray *output,
                        CudaNdarray *im, cudnnConvolutionDescriptor_t desc,
                        float alpha, float beta, int nb_dim, CudaNdarray **input) {
  cudnnStatus_t err = CUDNN_STATUS_SUCCESS;

  if (CudaNdarray_HOST_DIMS(im)[1] != CudaNdarray_HOST_DIMS(kerns)[1]) {
    PyErr_SetString(PyExc_ValueError,
		    "GpuDnnConv images and kernel must have the same stack size\n");
    return 1;
  }

  if (c_set_tensorNd(output, nb_dim, APPLY_SPECIFIC(output)) == -1)
    return 1;
  if (c_set_filterNd(kerns, nb_dim, APPLY_SPECIFIC(kerns)) == -1)
    return 1;

#ifdef CONV_INPLACE
  Py_XDECREF(*input);
  *input = im;
  Py_INCREF(*input);
#else
  if (CudaNdarray_prep_output(input, nb_dim, CudaNdarray_HOST_DIMS(im)) != 0)
    return 1;
  if (beta != 0.0 && CudaNdarray_CopyFromCudaNdarray(*input, im))
    return 1;
#endif

  if (c_set_tensorNd(*input, nb_dim, APPLY_SPECIFIC(input)) == -1)
    return 1;

  {
    size_t worksize;
    void *workspace;
    cudnnConvolutionBwdDataAlgo_t chosen_algo;

    if (CHOOSE_ALGO)
    {
      // Check if the kernels and the output have the same shape as they have
      // last time the apply node was executed
      bool same_shapes = true;
      for (int i = 0; (i < nb_dim) && same_shapes; i++)
      {
          same_shapes &= (CudaNdarray_HOST_DIMS(kerns)[i] ==
                          APPLY_SPECIFIC(previous_kerns_shape)[i]);
          same_shapes &= (CudaNdarray_HOST_DIMS(output)[i] ==
                          APPLY_SPECIFIC(previous_output_shape)[i]);
      }

      if (!same_shapes)
      {
        // The shape of the kernels and/or the output is different from the
        // last execution. Use the current shapes to infer the implementation
        // to use from now on.

        // Get the amount of available memory
        size_t free = 0, total = 0;
        cudaError_t err2 = cudaMemGetInfo(&free, &total);
        if (err2 != cudaSuccess){
          cudaGetLastError();
          fprintf(stderr,
                  "Error when trying to find the memory information"
                  " on the GPU: %s\n", cudaGetErrorString(err2));
          return 1;
        }

        // Use heuristics to choose the implementation
        err = cudnnGetConvolutionBackwardDataAlgorithm(_handle,
                                                       APPLY_SPECIFIC(kerns),
                                                       APPLY_SPECIFIC(output),
                                                       desc,
                                                       APPLY_SPECIFIC(input),
                                                       CUDNN_CONVOLUTION_BWD_DATA_PREFER_FASTEST,
                                                       free,
                                                       &chosen_algo);

        if (err != CUDNN_STATUS_SUCCESS) {
          PyErr_Format(PyExc_RuntimeError,
                       "GpuDnnConvGradI: error selecting convolution algo: %s",
                       cudnnGetErrorString(err));
          return 1;
        }

        // Store the shapes of the kernels and output as well as the chosen
        // algorithm for future use.
        APPLY_SPECIFIC(previous_bwd_d_algo) = chosen_algo;
        for (int i = 0; i < nb_dim; i++)
        {
            APPLY_SPECIFIC(previous_kerns_shape)[i] =
                                            CudaNdarray_HOST_DIMS(kerns)[i];
            APPLY_SPECIFIC(previous_output_shape)[i] =
                                            CudaNdarray_HOST_DIMS(output)[i];
        }

      }
      else
      {
        // The shapes of the kernels and the output are the same as for the
        // last execution. The convolution algorithm used last time can also
        // be used here
        chosen_algo = APPLY_SPECIFIC(previous_bwd_d_algo);
      }
    }
    else
    {
        chosen_algo = CONV_ALGO;
    }

    // The FFT implementation does not support strides, 1x1 filters or
    // inputs with a spatial dimension larger than 1024.
    // If the chosen implementation is FFT, validate that it can be used
    // on the current data and default on a safe implementation if it
    // can't.
    if (chosen_algo == CUDNN_CONVOLUTION_BWD_DATA_ALGO_FFT && nb_dim == 4)
    {

      // Extract the properties of the convolution descriptor
      int pad_h, pad_w, stride_v, stride_h, upscale_x, upscale_y;
      cudnnConvolutionMode_t mode;
      err = cudnnGetConvolution2dDescriptor(desc, &pad_h, &pad_w,
                                            &stride_v, &stride_h,
                                            &upscale_x, &upscale_y,
                                            &mode);

      if (err != CUDNN_STATUS_SUCCESS) {
        PyErr_Format(PyExc_RuntimeError,
                     "GpuDnnConvGradI: error getting convolution properties: %s",
                     cudnnGetErrorString(err));
        return 1;
      }

      // Extract the spatial size of the filters
      int filter_h = CudaNdarray_HOST_DIMS(kerns)[3];
      int filter_w = CudaNdarray_HOST_DIMS(kerns)[4];

      // Extract the spatial size of the input
      int input_h = CudaNdarray_HOST_DIMS(*input)[3];
      int input_w = CudaNdarray_HOST_DIMS(*input)[4];

      // Ensure that the selected implementation supports the requested
      // convolution. Fall back to a safe implementation otherwise.
      if (stride_v != 1 || stride_h != 1 || input_h > 1024 ||
          input_w > 1024 || (filter_h == 1 && filter_w == 1))
      {
        chosen_algo = CUDNN_CONVOLUTION_BWD_DATA_ALGO_1;
      }
    }

    // Infer required workspace size from the chosen implementation
    err = cudnnGetConvolutionBackwardDataWorkspaceSize(_handle,
                                                       APPLY_SPECIFIC(kerns),
                                                       APPLY_SPECIFIC(output),
                                                       desc,
                                                       APPLY_SPECIFIC(input),
                                                       chosen_algo,
                                                       &worksize);
    if (err != CUDNN_STATUS_SUCCESS) {
      PyErr_Format(PyExc_RuntimeError,
                   "GpuDnnConvGradI: error getting worksize: %s",
                   cudnnGetErrorString(err));
      return 1;
    }

    // Allocate workspace for the convolution
    workspace = get_work_mem(worksize);
    if (workspace == NULL && worksize != 0)
      return 1;

    // Perform the convolution
    err = cudnnConvolutionBackwardData_v3(
      _handle,
      (void *)&alpha,
      APPLY_SPECIFIC(kerns), CudaNdarray_DEV_DATA(kerns),
      APPLY_SPECIFIC(output), CudaNdarray_DEV_DATA(output),
      desc,
      chosen_algo,
      workspace, worksize,
      (void *)&beta,
      APPLY_SPECIFIC(input), CudaNdarray_DEV_DATA(*input));
  }

  if (err != CUDNN_STATUS_SUCCESS) {
    PyErr_Format(PyExc_RuntimeError, "GpuDnnConvGradI: error doing operation: %s",
                 cudnnGetErrorString(err));
    return 1;
  }
  return 0;
}

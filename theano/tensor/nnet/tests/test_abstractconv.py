import unittest
import numpy
import copy

import theano
import theano.tensor as T
from theano.tests import unittest_tools as utt

from nose.plugins.skip import SkipTest
import theano.tensor.nnet.conv as conv_ref
import theano.tensor.nnet.abstract_conv2d as conv

from theano.sandbox.cuda import float32_shared_constructor as gpu_shared
from theano.compile import shared as cpu_shared

from theano.sandbox.cuda.tests.test_conv_cuda_ndarray import py_conv
from theano.sandbox.cuda.dnn import dnn_available, dnn_conv, dnn_gradweight, dnn_gradinput


if theano.config.mode == 'FAST_COMPILE':
    mode_with_gpu = theano.compile.mode.get_mode('FAST_RUN').including('gpu')
    mode_without_gpu = theano.compile.mode.get_mode('FAST_RUN').excluding('gpu')
else:
    mode_with_gpu = theano.compile.mode.get_default_mode().including('gpu')
    mode_without_gpu = theano.compile.get_default_mode().excluding('gpu')


class TestConv2d(unittest.TestCase):

    def setUp(self):
        super(TestConv2d, self).setUp()

        self.inputs_shapes = [(16, 1, 12, 12), (16, 1, 18, 18), (2, 1, 24, 24),
                              (6, 1, 20, 20), (2, 1, 32, 20), (1, 5, 32, 32)]
        self.filters_shapes = [(10, 1, 2, 2), (10, 1, 3, 3), (10, 1, 2, 2),
                               (1, 1, 2, 5), (5, 1, 2, 2), (15, 5, 2, 2)]
        self.subsamples = [(1, 1), (2, 2), (2, 4)]
        self.border_modes = ["valid", "full", (0, 0), (1, 1), (5, 5), (5, 2)]


    def get_output_shape(self, inputs_shape, filters_shape, subsample, border_mode):
        if border_mode == "valid":
            border_mode = (0, 0)
        if border_mode == "full":
            border_mode = (filters_shape[2] - 1, filters_shape[3] - 1)
        batch_size = inputs_shape[0]
        num_filters = filters_shape[0]
        return (batch_size, num_filters,) \
            + tuple(None if i is None or k is None
                    else ((i + 2*pad - k) // d + 1)
                    for i, k, d, pad in zip(inputs_shape[2:], filters_shape[2:],
                                            subsample, border_mode))

    def run_fwd(self, inputs_shape, filters_shape, ref=dnn_conv,
                subsample=(1, 1), verify_grad=True, mode=mode_without_gpu,
                border_mode='valid', filters_flip=True, device='cpu', provide_shape=False):

        inputs_val = numpy.random.random(inputs_shape).astype('float32')
        filters_val = numpy.random.random(filters_shape).astype('float32')
        if device == 'gpu':
            inputs = gpu_shared(inputs_val)
            filters = gpu_shared(filters_val)
        else:
            inputs = theano.tensor.as_tensor_variable(cpu_shared(inputs_val))
            filters = theano.tensor.as_tensor_variable(cpu_shared(filters_val))
        if provide_shape:
            imshp = inputs_shape
            kshp = filters_shape
        else:
            imshp = None
            kshp = None
        if filters_flip:
            conv_mode = 'conv'
        else:
            conv_mode = 'cross'

        c_ref = ref(inputs, filters,
                    border_mode=border_mode,
                    subsample=subsample,
                    conv_mode = conv_mode)
        c = conv.conv2d(inputs, filters,
                        border_mode=border_mode,
                        subsample=subsample,
                        filters_flip=filters_flip,
                        inputs_shape=imshp,
                        filters_shape=kshp)
        f_ref = theano.function([], c_ref, mode=mode)
        f = theano.function([], c, mode)
        res_ref = numpy.array(f_ref())
        res = numpy.array(f())
        utt.assert_allclose(res_ref, res)
        if verify_grad:
            utt.verify_grad(conv.AbstractConv2d(border_mode="valid", imshp=imshp, kshp=kshp,
                                                bsize=inputs_shape[0], subsample=subsample),
                            [inputs_val, filters_val],
                            mode=mode)

    def run_gradweight(self, inputs_shape, filters_shape, output_shape,
                       ref=dnn_gradweight, subsample=(1, 1), filters_flip=True,
                       verify_grad=True, mode=mode_without_gpu, border_mode='valid',
                       device='cpu', provide_shape = False):

        inputs_val = numpy.random.random(inputs_shape).astype('float32')
        output_val = numpy.random.random(output_shape).astype('float32')
        if device == 'gpu':
            inputs = gpu_shared(inputs_val)
            output = gpu_shared(output_val)
        else:
            inputs = theano.tensor.as_tensor_variable(cpu_shared(inputs_val))
            output = theano.tensor.as_tensor_variable(cpu_shared(output_val))
        if provide_shape:
            imshp = inputs_shape
            kshp = filters_shape
        else:
            imshp = None
            kshp = None
        if filters_flip:
            conv_mode = 'conv'
        else:
            conv_mode = 'cross'
        c = conv.AbstractConv2d_gradWeights(border_mode=border_mode,
                                            filters_flip=filters_flip,
                                            subsample=subsample,
                                            imshp = imshp, kshp = kshp)
        c = c(inputs, output, filters_shape[-2:])
        c_ref = ref(inputs, output,
                    filters_shape,
                    border_mode=border_mode,
                    subsample=subsample,
                    conv_mode=conv_mode)
        f = theano.function([], c, mode)
        f_ref = theano.function([], c_ref, mode)
        res_ref = numpy.array(f_ref())
        res = numpy.array(f())
        utt.assert_allclose(res_ref, res)

        def abstract_conv2d_gradweight(inputs_val, output_val):
            conv_op = conv.AbstractConv2d_gradWeights(border_mode=border_mode, subsample=subsample)
            return conv_op(inputs_val, output_val, filters_shape[-2:])

        def cudnn_gradweight(inputs_val, output_val):
            c_ref = ref(inputs, output,
                        filters_shape,
                        border_mode=border_mode,
                        subsample=subsample,
                        conv_mode=conv_mode)
            return c_ref

        if verify_grad:
            #utt.verify_grad(abstract_conv2d_gradweight, [inputs_val, output_val],
            #                mode=mode)
            utt.verify_grad(cudnn_gradweight, [inputs_val, output_val], mode=mode)


    def run_gradinput(self, inputs_shape, filters_shape, output_shape, ref=dnn_gradinput,
                      subsample=(1, 1), filters_flip=True, verify_grad=True, mode=mode_without_gpu,
                      border_mode='valid', device='cpu', provide_shape = False):


        output_val = numpy.random.random(output_shape).astype('float32')
        filters_val = numpy.random.random(filters_shape).astype('float32')
        if device == 'gpu':
            output = gpu_shared(output_val)
            filters = gpu_shared(filters_val)
        else:
            output = theano.tensor.as_tensor_variable(cpu_shared(output_val))
            filters = theano.tensor.as_tensor_variable(cpu_shared(filters_val))
        if provide_shape:
            imshp = inputs_shape
            kshp = filters_shape
        else:
            imshp = None
            kshp = None
        if filters_flip:
            conv_mode = 'conv'
        else:
            conv_mode = 'cross'
        c = conv.AbstractConv2d_gradInputs(border_mode=border_mode,
                                           subsample=subsample,
                                           filters_flip=filters_flip,
                                           imshp=imshp, kshp=kshp)
        c = c(filters, output, inputs_shape[-2:])
        c_ref = ref(filters, output, inputs_shape,
                    border_mode=border_mode, subsample=subsample,
                    conv_mode=conv_mode)
        f = theano.function([], c, mode)
        f_ref = theano.function([], c_ref, mode)
        res_ref = numpy.array(f_ref())
        res = numpy.array(f())
        utt.assert_allclose(res_ref, res)

        def abstract_conv2d_gradinputs(filters_val, output_val):
            conv_op = conv.AbstractConv2d_gradInputs(border_mode=border_mode, subsample=subsample)
            return conv_op(filters_val, output_val, inputs_shape[-2:])
        if verify_grad:
            utt.verify_grad(abstract_conv2d_gradinputs, [filters_val, output_val],
                            mode=mode)


    def test_dnn_conv(self):
        if not dnn_available():
            return
        mode=mode_with_gpu

        inputs_shapes =  self.inputs_shapes
        filters_shapes = self.filters_shapes
        subsamples = self.subsamples
        border_modes = self.border_modes
        for i, f in zip(inputs_shapes[0:1], filters_shapes[0:1]):
            for s in subsamples:
                for b in border_modes:
                    o = self.get_output_shape(i, f, s, b)
                    for provide_shape in [False, True]:
                        self.run_fwd(inputs_shape=i, filters_shape=f, subsample=s,
                                     verify_grad=True, mode=mode, device='gpu',
                                     provide_shape=provide_shape, border_mode=b)
                        self.run_gradweight(inputs_shape=i, filters_shape=f,
                                            output_shape=o, subsample=s,
                                            verify_grad=False, mode=mode, device='gpu',
                                            provide_shape=provide_shape, border_mode=b)
                        self.run_gradinput(inputs_shape=i, filters_shape=f,
                                           output_shape=o, subsample=s,
                                           verify_grad=False, mode=mode, device='gpu',
                                           provide_shape=provide_shape, border_mode=b)

    def test_cormm_conv(self):
        mode = mode_with_gpu.excluding('cudnn')

        inputs_shapes =  self.inputs_shapes
        filters_shapes = self.filters_shapes
        subsamples = self.subsamples
        border_modes = self.border_modes
        for i, f in zip(inputs_shapes, filters_shapes):
            for s in subsamples:
                for b in border_modes:
                    o = self.get_output_shape(i, f, s, b)
                    for provide_shape in [False, True]:
                        self.run_fwd(inputs_shape=i, filters_shape=f, subsample=s,
                                     verify_grad=True, mode=mode, device='gpu',
                                     provide_shape=provide_shape, border_mode=b)
                        self.run_gradweight(inputs_shape=i, filters_shape=f,
                                            output_shape=o, subsample=s,
                                            verify_grad=False, mode=mode, device='gpu',
                                            provide_shape=provide_shape, border_mode=b)
                        self.run_gradinput(inputs_shape=i, filters_shape=f,
                                           output_shape=o, subsample=s,
                                           verify_grad=False, mode=mode, device='gpu',
                                           provide_shape=provide_shape, border_mode=b)





    def test_cpu_conv(self):
        mode = mode_without_gpu

        inputs_shapes =  self.inputs_shapes
        filters_shapes = self.filters_shapes
        subsamples = self.subsamples
        border_modes = self.border_modes[:2] # only valid and full are supported

        for i, f in zip(inputs_shapes, filters_shapes):
            for s in subsamples:
                for b in border_modes:
                    o = self.get_output_shape(i, f, s, b)
                    for provide_shape in [False, True]:
                        self.run_fwd(inputs_shape=i, filters_shape=f, subsample=s,
                                     verify_grad=True, mode=mode, device='cpu',
                                     provide_shape=provide_shape, border_mode=b)
                        self.run_gradweight(inputs_shape=i, filters_shape=f,
                                            output_shape=o, subsample=s,
                                            verify_grad=False, mode=mode, device='cpu',
                                            provide_shape=provide_shape, border_mode=b)
                        self.run_gradinput(inputs_shape=i, filters_shape=f,
                                           output_shape=o, subsample=s,
                                           verify_grad=False, mode=mode, device='cpu',
                                           provide_shape=provide_shape, border_mode=b)

import unittest
import numpy
import itertools

import theano
from theano.tests import unittest_tools as utt
import theano.tensor.nnet.abstract_conv2d as conv
from theano.sandbox.cuda import float32_shared_constructor as gpu_shared
from theano.compile import shared as cpu_shared
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
        self.input_shapes = [(8, 1, 12, 12), (8, 1, 18, 18), (2, 1, 4, 4),
                             (6, 1, 10, 11), (2, 1, 6, 5), (1, 5, 9, 9)]
        self.filter_shapes = [(5, 1, 2, 2), (4, 1, 3, 3), (2, 1, 3, 3),
                              (1, 1, 2, 5), (4, 1, 2, 2), (4, 5, 2, 2)]
        self.subsamples = [(1, 1), (2, 2), (2, 4)]
        self.border_modes = ["valid", "full", (0, 0), (1, 1), (5, 5), (5, 2)]
        self.filters_flip = [True, False]

    def get_output_shape(self, input_shape, filter_shape, subsample, border_mode):

        if border_mode == "valid":
            border_mode = (0, 0)
        if border_mode == "full":
            border_mode = (filter_shape[2] - 1, filter_shape[3] - 1)
        batch_size = input_shape[0]
        num_filters = filter_shape[0]
        return (batch_size, num_filters,) \
            + tuple(None if i is None or k is None
                    else ((i + 2 * pad - k) // d + 1)
                    for i, k, d, pad in zip(input_shape[2:], filter_shape[2:],
                                            subsample, border_mode))

    def run_fwd(self, input_shape, filter_shape, ref=dnn_conv,
                subsample=(1, 1), verify_grad=True, mode=mode_without_gpu,
                border_mode='valid', filters_flip=True, device='cpu', provide_shape=False):

        inputs_val = numpy.random.random(input_shape).astype('float32')
        filters_val = numpy.random.random(filter_shape).astype('float32')
        if device == 'gpu':
            inputs = gpu_shared(inputs_val)
            filters = gpu_shared(filters_val)
        else:
            inputs = theano.tensor.as_tensor_variable(cpu_shared(inputs_val))
            filters = theano.tensor.as_tensor_variable(cpu_shared(filters_val))
        if provide_shape:
            imshp = input_shape
            kshp = filter_shape
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
                    conv_mode=conv_mode)
        c = conv.conv2d(inputs, filters,
                        border_mode=border_mode,
                        subsample=subsample,
                        filters_flip=filters_flip,
                        input_shape=imshp,
                        filter_shape=kshp)
        f_ref = theano.function([], c_ref, mode=mode)
        f = theano.function([], c, mode)
        res_ref = numpy.array(f_ref())
        res = numpy.array(f())
        utt.assert_allclose(res_ref, res)
        if verify_grad:
            utt.verify_grad(conv.AbstractConv2d(border_mode="valid", imshp=imshp, kshp=kshp,
                                                subsample=subsample),
                            [inputs_val, filters_val],
                            mode=mode)

    def run_gradweight(self, input_shape, filter_shape, output_shape,
                       ref=dnn_gradweight, subsample=(1, 1), filters_flip=True,
                       verify_grad=True, mode=mode_without_gpu, border_mode='valid',
                       device='cpu', provide_shape = False):

        inputs_val = numpy.random.random(input_shape).astype('float32')
        output_val = numpy.random.random(output_shape).astype('float32')
        if device == 'gpu':
            inputs = gpu_shared(inputs_val)
            output = gpu_shared(output_val)
        else:
            inputs = theano.tensor.as_tensor_variable(cpu_shared(inputs_val))
            output = theano.tensor.as_tensor_variable(cpu_shared(output_val))
        if provide_shape:
            imshp = input_shape
            kshp = filter_shape
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
                                            imshp=imshp, kshp=kshp)
        c = c(inputs, output, filter_shape[-2:])
        c_ref = ref(inputs, output,
                    filter_shape,
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
            return conv_op(inputs_val, output_val, filter_shape[-2:])

        if verify_grad:
            utt.verify_grad(abstract_conv2d_gradweight, [inputs_val, output_val],
                            mode=mode, eps=1)

    def run_gradinput(self, input_shape, filter_shape, output_shape, ref=dnn_gradinput,
                      subsample=(1, 1), filters_flip=True, verify_grad=True, mode=mode_without_gpu,
                      border_mode='valid', device='cpu', provide_shape = False):

        output_val = numpy.random.random(output_shape).astype('float32')
        filters_val = numpy.random.random(filter_shape).astype('float32')
        if device == 'gpu':
            output = gpu_shared(output_val)
            filters = gpu_shared(filters_val)
        else:
            output = theano.tensor.as_tensor_variable(cpu_shared(output_val))
            filters = theano.tensor.as_tensor_variable(cpu_shared(filters_val))
        if provide_shape:
            imshp = input_shape
            kshp = filter_shape
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
        c = c(filters, output, input_shape[-2:])
        c_ref = ref(filters, output, input_shape,
                    border_mode=border_mode, subsample=subsample,
                    conv_mode=conv_mode)
        f = theano.function([], c, mode)
        f_ref = theano.function([], c_ref, mode)
        res_ref = numpy.array(f_ref())
        res = numpy.array(f())
        utt.assert_allclose(res_ref, res)

        def abstract_conv2d_gradinputs(filters_val, output_val):
            conv_op = conv.AbstractConv2d_gradInputs(border_mode=border_mode, subsample=subsample)
            return conv_op(filters_val, output_val, input_shape[-2:])
        if verify_grad:
            utt.verify_grad(abstract_conv2d_gradinputs, [filters_val, output_val],
                            mode=mode, eps=1)

    def test_dnn_conv(self):
        if not dnn_available():
            return
        mode = mode_with_gpu
        # provide_shape is not used by the CuDNN impementation
        provide_shape = False

        for (i, f), s, b, flip in itertools.product(
                zip(self.input_shapes, self.filter_shapes),
                self.subsamples,
                self.border_modes,
                self.filters_flip):
            o = self.get_output_shape(i, f, s, b)
            self.run_fwd(input_shape=i, filter_shape=f, subsample=s,
                         verify_grad=True, mode=mode, device='gpu',
                         provide_shape=provide_shape, border_mode=b,
                         filters_flip=flip)
            self.run_gradweight(input_shape=i, filter_shape=f,
                                output_shape=o, subsample=s,
                                verify_grad=True, mode=mode, device='gpu',
                                provide_shape=provide_shape, border_mode=b,
                                filters_flip=flip)
            self.run_gradinput(input_shape=i, filter_shape=f,
                               output_shape=o, subsample=s,
                               verify_grad=True, mode=mode, device='gpu',
                               provide_shape=provide_shape, border_mode=b,
                               filters_flip=flip)

    def test_cormm_conv(self):
        mode = mode_with_gpu.excluding('cudnn')

        for (i, f), s, b, flip, provide_shape in itertools.product(
                zip(self.input_shapes, self.filter_shapes),
                self.subsamples,
                self.border_modes,
                self.filters_flip,
                [False, True]):

            o = self.get_output_shape(i, f, s, b)
            self.run_fwd(input_shape=i, filter_shape=f, subsample=s,
                         verify_grad=True, mode=mode, device='gpu',
                         provide_shape=provide_shape, border_mode=b,
                         filters_flip=flip)
            self.run_gradweight(input_shape=i, filter_shape=f,
                                output_shape=o, subsample=s,
                                verify_grad=True, mode=mode, device='gpu',
                                provide_shape=provide_shape, border_mode=b,
                                filters_flip=flip)
            self.run_gradinput(input_shape=i, filter_shape=f,
                               output_shape=o, subsample=s,
                               verify_grad=True, mode=mode, device='gpu',
                               provide_shape=provide_shape, border_mode=b,
                               filters_flip=flip)

    def test_cpu_conv(self):
        mode = mode_without_gpu

        for (i, f), s, b, flip, provide_shape in itertools.product(
                zip(self.input_shapes, self.filter_shapes),
                self.subsamples,
                self.border_modes,
                self.filters_flip,
                [False, True]):

            o = self.get_output_shape(i, f, s, b)
            fwd_OK = True
            gradweight_OK = True
            gradinput_OK = True

            if not flip:
                fwd_OK = False
                gradweight_OK = False
                gradinput_OK = False

            if b not in ('valid', 'full'):
                fwd_OK = False
                gradweight_OK = False
                gradinput_OK = False

            if (not provide_shape) and (s != (1, 1)) and (b == 'full'):
                gradweight_OK = False
                gradinput_OK = False

            if ((s[0] not in (1, 2)) or (s[1] not in (1, 2))) and (b == 'full'):
                gradweight_OK = False
                gradinput_OK = False

            if fwd_OK:
                self.run_fwd(input_shape=i, filter_shape=f, subsample=s,
                             verify_grad=True, mode=mode, device='cpu',
                             provide_shape=provide_shape, border_mode=b,
                             filters_flip=flip)
            else:
                self.assertRaises(NotImplementedError,
                                  self.run_fwd,
                                  input_shape=i,
                                  filter_shape=f,
                                  subsample=s,
                                  verify_grad=False,
                                  mode=mode,
                                  device='cpu',
                                  provide_shape=provide_shape,
                                  border_mode=b,
                                  filters_flip=flip)

            if gradweight_OK:
                self.run_gradweight(input_shape=i, filter_shape=f,
                                    output_shape=o, subsample=s,
                                    verify_grad=False, mode=mode, device='cpu',
                                    provide_shape=provide_shape, border_mode=b,
                                    filters_flip=flip)
            else:
                self.assertRaises(NotImplementedError,
                                  self.run_gradweight,
                                  input_shape=i,
                                  filter_shape=f,
                                  output_shape=o,
                                  subsample=s,
                                  verify_grad=False,
                                  mode=mode,
                                  device='cpu',
                                  provide_shape=provide_shape,
                                  border_mode=b,
                                  filters_flip=flip)

            if gradinput_OK:
                self.run_gradinput(input_shape=i, filter_shape=f,
                                   output_shape=o, subsample=s,
                                   verify_grad=False, mode=mode, device='cpu',
                                   provide_shape=provide_shape, border_mode=b,
                                   filters_flip=flip)
            else:
                self.assertRaises(NotImplementedError,
                                  self.run_gradinput,
                                  input_shape=i,
                                  filter_shape=f,
                                  output_shape=o,
                                  subsample=s,
                                  verify_grad=False,
                                  mode=mode,
                                  device='cpu',
                                  provide_shape=provide_shape,
                                  border_mode=b,
                                  filters_flip=flip)

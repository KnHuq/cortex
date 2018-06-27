'''Builds arch

'''

import logging
import time

from . import data, optimizer
from .parsing import parse_docstring, parse_inputs, parse_kwargs
from .handlers import (aliased, prefixed, NetworkHandler, LossHandler,
                       ResultsHandler)
from .utils import bad_values, update_dict_of_lists
from .viz import VizHandler


__author__ = 'R Devon Hjelm'
__author_email__ = 'erroneus@gmail.com'

logger = logging.getLogger('cortex.models')

MODEL_PLUGINS = {}


def register_model(plugin):
    '''

    Args:
        plugin: TODO

    Returns:
        TODO

    '''

    global MODEL_PLUGINS

    if plugin.__name__ in MODEL_PLUGINS:
        raise KeyError('{} already registered under the same name.'
                       .format(plugin.__name__))

    MODEL_PLUGINS[plugin.__name__] = plugin()


def get_model(model_name):
    try:
        return MODEL_PLUGINS[model_name]
    except KeyError:
        raise KeyError('Model {} not found. Available: {}'
                       .format(model_name, tuple(MODEL_PLUGINS.keys())))


class PluginType(type):
    def __new__(metacls, name, bases, attrs):
        cls = super(PluginType, metacls).__new__(metacls, name, bases, attrs)

        help = {}
        kwargs = {}
        args = set()

        for key in ['build', 'routine', 'visualize', 'train_step', 'eval_step']:
            if hasattr(cls, key):
                attr = getattr(cls, key)
                help_ = parse_docstring(attr)
                kwargs_ = parse_kwargs(attr)
                args_ = set(parse_inputs(attr))

                for k, v in help_.items():
                    if k in help and v != help[k]:
                        metacls._warn_inconsitent_help(key, k, v, kwargs[k])

                for k, v in kwargs_.items():
                    if k in kwargs and v != kwargs[k]:
                        metacls._warn_inconsitent_kwargs(key, k, v, kwargs[k])

                help.update(**help_)
                kwargs.update(**kwargs_)
                args |= args_

        cls._help = help
        cls._kwargs = kwargs
        cls._args = args

        return cls

    def _warn_inconsitent_help(cls, k, v, v_):
        logger.warning('Inconsistent docstring found with argument {k}. '
                       'Using {v} instead of {v_}'.format(k=k, v=v, v_=v_))

    def _warn_inconsitent_kwargs(cls, k, v, v_):
        logger.warning('Inconsistent keyword defaults found with argument {k}. '
                       'Using {v} instead of {v_}'.format(k=k, v=v, v_=v_))


class ModelPluginBase(metaclass=PluginType):
    '''
    TODO
    '''

    _viz = VizHandler()
    _data = data.DATA_HANDLER
    _optimizers = optimizer.OPTIMIZERS

    _kwargs = dict()
    _help = dict()
    _owners = dict()
    _training_nets = dict()

    _kwarg_dict = dict()
    _input_dict = dict()

    _all_nets = NetworkHandler(allow_overwrite=False)
    _all_losses = LossHandler(_all_nets, allow_overwrite=False)
    _all_results = ResultsHandler()

    _all_epoch_results = ResultsHandler()
    _all_epoch_losses = ResultsHandler()
    _all_epoch_times = ResultsHandler()

    def __init__(self, contract=None):
        '''

        Args:
            contract: TODO
        '''

        self._contract = None
        self._train = False

        if contract:
            contract = self._check_contract(contract)
            self._accept_contract(contract)

        if self._contract and len(self._contract['nets']) > 0:
            self._nets = aliased(self._all_nets, aliases=contract['nets'])
            self._losses = aliased(self._all_losses, aliases=contract['nets'])
            self._epoch_losses = aliased(
                self._all_epoch_losses, aliases=contract['nets'])
        else:
            self._nets = aliased(self._all_nets)
            self._losses = aliased(self._all_losses)
            self._epoch_losses = aliased(self._all_epoch_losses)

        for k in ['build', 'routine', 'visualize', 'train_step',
                  'eval_step']:
            fn = getattr(self, k)
            fid = self._get_id(fn)
            self._owners[fid] = self.__class__.__name__

        self._wrap_build()
        self._wrap_routine()
        self.train_step = self._wrap_step(self.train_step)
        self.eval_step = self._wrap_step(self.eval_step, train=False)
        self.train_loop = self._wrap_loop(self.train_loop, train=True)
        self.eval_loop = self._wrap_loop(self.eval_loop, train=False)

        self._results = prefixed(
            self._all_results, prefix=self.__class__.__name__)
        self._epoch_results = prefixed(
            self._all_epoch_results, prefix=self.__class__.__name__)
        self._epoch_times = self._all_epoch_times

    @classmethod
    def _reset_class(cls):
        '''

        Returns:
            TODO

        '''
        cls._kwargs.clear()
        cls._help.clear()
        cls._owners.clear()
        cls._training_nets.clear()

        cls._kwarg_dict = dict()
        cls._input_dict = dict()

        cls._all_nets.clear()
        cls._all_losses.clear()
        cls._all_results.clear()

        cls._all_epoch_results.clear()
        cls._all_epoch_losses.clear()
        cls._all_epoch_times.clear()

    def _reset_epoch(self):
        self._all_epoch_results.clear()
        self._all_epoch_losses.clear()
        self._all_epoch_times.clear()

    def _get_id(self, fn):
        '''

        Args:
            fn: TODO

        Returns:
            TODO

        '''
        return fn

    @property
    def kwargs(self):
        return self._kwargs

    @property
    def args(self):
        return self._args

    @property
    def help(self):
        return self._help

    @property
    def results(self):
        return self._results

    @property
    def epoch_results(self):
        return self._epoch_results

    @property
    def epoch_losses(self):
        return self._epoch_losses

    @property
    def epoch_times(self):
        return self._epoch_times

    @property
    def nets(self):
        return self._nets

    @property
    def losses(self):
        return self._losses

    @property
    def viz(self):
        return self._viz

    @property
    def data(self):
        return self._data

    def __setattr__(self, key, value):
        '''

        Args:
            key: TODO
            value: TODO

        Returns:
            TODO

        '''
        if isinstance(value, ModelPluginBase):
            model = value
            kwargs = model.kwargs
            help = model.help
            if model._contract:
                kwargs = dict((model._contract['kwargs'].get(k, k), v)
                              for k, v in kwargs.items())
                help = dict((model._contract['kwargs'].get(k, k), v)
                            for k, v in help.items())
            for k, v in kwargs.items():
                if k not in self.kwargs:
                    self.kwargs[k] = v
                if k not in self.help:
                    self.help[k] = help[k]

        super().__setattr__(key, value)

    def _check_contract(self, contract):
        '''

        Args:
            contract: TODO

        Returns:
            TODO

        '''
        kwargs = contract.pop('kwargs', {})
        nets = contract.pop('nets', {})
        inputs = contract.pop('inputs', {})

        if len(contract) > 0:
            raise KeyError('Unknown keys in contract: {}'
                           .format(tuple(contract.keys())))

        for k, v in kwargs.items():
            if k not in self.kwargs:
                raise KeyError('Invalid contract: {} does not have any '
                               'arguments called {}'
                               .format(self.__class__.__name__, k))

            if not isinstance(v, str):
                raise TypeError('Contract values must be strings.')

        for k, v in inputs.items():
            if k not in self.args:
                raise KeyError('Invalid contract: {} does not have any '
                               'inputs called {}'
                               .format(self.__class__.__name__, k))

            if not isinstance(v, str):
                raise TypeError('Contract values must be strings.')

        return dict(inputs=inputs, kwargs=kwargs, nets=nets)

    def _accept_contract(self, contract):
        '''

        Args:
            contract: TODO

        Returns:
            TODO

        '''
        if self._contract is not None:
            raise ValueError('Cannot accept more than one contract.')

        self._contract = contract

        for k in ['build', 'routine', 'visualize', 'train_step',
                  'eval_step']:
            fn = getattr(self, k)
            fid = self._get_id(fn)
            self._kwarg_dict[fid] = contract['kwargs']
            self._input_dict[fid] = contract['inputs']

    def _wrap_build(self):
        fn = self.build

        def wrapped(*args, **kwargs):
            return fn(*args, **kwargs)

        wrapped._fn = fn
        self.build = wrapped

    def _wrap_routine(self):
        '''

        Set to `requires_grad` for models that are trained with this routine.

        Args:
            routine: TODO

        '''

        fn = self.routine

        def wrapped(*args, **kwargs):
            fid = self._get_id(fn)

            if fid not in self._training_nets:
                losses_before = dict(kv for kv in self._all_losses.items())
                fn(*args, **kwargs)
                losses_after = dict(kv for kv in self._all_losses.items())

                training_nets = []

                for k, v in losses_after.items():
                    if k not in losses_before:
                        training_nets.append(k)
                    elif v != losses_before[k]:
                        training_nets.append(k)
                self._training_nets[fid] = training_nets
                for k in training_nets:
                    self.losses.pop(k)
            else:
                training_nets = self._training_nets[fid]

            if self._train:
                for k in training_nets:
                    net = self.nets[k]
                    self._optimizers[k].zero_grad()
                    for p in net.parameters():
                        p.requires_grad = k in training_nets
                    net.train()

            start = time.time()
            output = fn(*args, **kwargs)
            self._check_bad_values()
            end = time.time()

            owner = self._owners[self._get_id(fn)]
            update_dict_of_lists(self.epoch_results, **self.results)
            update_dict_of_lists(self.epoch_times, **{owner: end - start})
            losses = dict((k, v.item()) for k, v in self.losses.items())
            update_dict_of_lists(self.epoch_losses, **losses)

            return output

        wrapped._fn = fn
        self.routine = wrapped

    def _wrap_step(self, fn, train=True):
        '''

        Args:
            fn: TODO
            train: TODO

        Returns:

        '''

        def wrapped(*args, **kwargs):
            if train:
                self._train = True
                for net in self.nets.values():
                    net.train()
            else:
                self._train = False
                for net in self.nets.values():
                    net.eval()

            self._all_losses.clear()
            self._all_results.clear()

            output = fn(*args, **kwargs)

            loss_keys = self.losses.keys()
            for key in loss_keys:
                self.losses.pop(key)

            return output

        wrapped._fn = fn
        return wrapped

    def _wrap_loop(self, fn, train=True):
        '''

        Args:
            fn: TODO
            train: TODO

        Returns:

        '''

        data_mode = 'train' if train else 'test'

        def wrapped(epoch, data_mode=data_mode):
            self._reset_epoch()
            self.data.reset(data_mode,
                            string='Training (epoch {}): '.format(epoch))

            fn()

            results = self._all_epoch_results
            results['losses'] = dict(self._all_epoch_losses)
            results['times'] = dict(self._all_epoch_times)

        wrapped._fn = fn
        return wrapped

    def get_inputs(self, fn):
        '''

        Args:
            fn: TODO

        Returns:
            TODO

        '''

        fid = self._get_id(fn._fn)
        input_dict = self._input_dict.get(fid, {})
        input_keys = parse_inputs(fn._fn)

        inputs = []
        for k in input_keys:
            key = input_dict.get(k, k)
            inp = self.data[key]
            inputs.append(inp)

        return inputs

    def get_kwargs(self, fn):
        '''

        Args:
            fn: TODO

        Returns:
            TODO

        '''

        fid = self._get_id(fn._fn)
        kwarg_dict = self._kwarg_dict.get(fid, {})
        kwarg_keys = parse_kwargs(fn._fn).keys()

        kwargs = dict()
        for k in kwarg_keys:
            key = kwarg_dict.get(k, k)
            value = self.kwargs.get(key, key)
            kwargs[k] = value

        return kwargs

    def _get_training_nets(self):
        '''

        Returns:
            TODO

        '''

        training_nets = []
        for v in self._training_nets.values():
            training_nets += v

        return training_nets

    def _check_bad_values(self):
        '''

        Check for bad numbers.

        Returns:
            TODO

        '''

        bads = bad_values(self.results)
        if bads:
            print(
                'Bad values found (quitting): {} \n All:{}'.format(
                    bads, self.results))
            exit(0)

        bads = bad_values(self.losses)
        if bads:
            print(
                'Bad values found (quitting): {} \n All:{}'.format(
                    bads, self.losses))
            exit(0)

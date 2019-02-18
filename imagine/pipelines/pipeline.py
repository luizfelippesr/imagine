import os
import numpy as np
from mpi4py import MPI
from keepers import Loggable

from imagine.likelihoods import Likelihood
from imagine.fields import GeneralFieldFactory
from imagine.simulators import Simulator
from imagine.priors import Prior
from imagine import pymultinest
'''
from scipy import optimize
from imagine.sample import Sample
'''

comm = MPI.COMM_WORLD
size = comm.size
rank = comm.rank

WORK_TAG = 0
DIE_TAG = 1

'''
Pipeline class defines methods for running Bayeisan analysis
The default sampler is pyMultinest

initialisation of Pipeline object requires following arguments:
* list/tuple of field factories, each factory is incharge of
generating field or field handles, to be under further machining
in simulator(s)
* Simulator object, may interface more than one external libraris,
but its not for Pipeline to concern
* Likelihood object
* Prior object

in design, simulator should have __call__ function taking
dict of field objects
and
list of observable names (defined in Likelihood class)
as arguments, and return a dict of observables:

simulator({factory.name: factory.generate(variables,...)}) -> {observable_name: observable_value}

where factory.generate take active variable dict as input

following this, likelihood object takes the output of simulator,
which is a dict of observables, and match with real data-set,
which would be nice, if the same dict structure applies

undeciphered:
.sample_callback with Sample class
.find_minimum

'''
class Pipeline(Loggable, object):

    '''
    field_factory -- list or tuple of FieldFactory objects
    simulator -- Simulator object, multi-simulator wrapping should be done in Simulator
    likelihood -- Likelihood object
    prior -- Prior object, multi-type-prior not supported yet
    '''
    def __init__(self, field_factory, simulator, likelihood, prior,
                 ensemble_size=1, pymultinest_parameters={}, sample_callback=None):
        self.logger.debug('setting up pipeline')
        self.field_factory = field_factory
        self.simulator = simulator
        self.likelihood = likelihood
        self.prior = prior
        self.ensemble_size = ensemble_size
        # setting defaults for pymultinest
        self.pymultinest_parameters = {'verbose': True,
                                       'n_iter_before_update': 1,
                                       'n_live_points': 100}
        self.pymultinest_parameters.update(pymultinest_parameters)
        self.sample_callback = sample_callback
        #
        # bonus controllers :)
        #
        # rescaling total likelihood in _core_likelihood
        self.likelihood_rescaler = 1.
        # seed for generating random field
        self.fixed_random_seed = None
        # checking likelihood threshold in _multinest_likelihood
        self.check_threshold = False
        self.likelihood_threshold = 0.

    @property
    def field_factory(self):
        return self._field_factory

    @field_factory.setter
    def field_factory(self, field_factory):
        assert isinstance(field_factory, (list,tuple))
        self.logger.debug('setting field_factory, registering active_variables')
        # record all active variables' name
        self.active_variables = ()
        # extract active_variables from each factory
        # notice that once done
        # the variable ordering is fixed wrt factory ordering
        # which is useful in recovering variable logic value for each factory
        # in _core_likelihood
        for ff in field_factory:#{
            assert isinstance(ff, FieldFactory)
            for av_name in ff.active_variables:#{
                assert isinstance(av_name, str)
                self.active_variables += (ff.name+'_'+av_name,)
            #}
        #}
        self._field_factory = field_factory
    
    @property
    def simulator(self):
        return self._simulator

    @simulator.setter
    def simulator(self, simulator):
        self.logger.debug('setting simulator')
        assert isinstance(simulator, Simulator)
        self._simulator = simulator

    @property
    def likelihood(self):
        return self._likelihood

    @likelihood.setter
    def likelihood(self, likelihood):
        self.logger.debug('setting likelihood')
        assert isinstance(likelihood, Likelihood)
        self._likelihood = likelihood

    @property
    def prior(self):
        return self._prior

    @prior.setter
    def prior(self, prior):
        self.logger.debug('setting prior')
        assert isinstance(prior, Prior)
        self._prior = prior

    @property
    def active_variables(self):
        return self._active_variables

    @property
    def ensemble_size(self):
        return self._ensemble_size

    @ensemble_size.setter
    def ensemble_size(self, ensemble_size):
        ensemble_size = int(ensemble_size)
        assert (ensemble_size > 0)
        self.logger.debug('Setting ensemble size to %i' % ensemble_size)
        self._ensemble_size = ensemble_size

    def __call__(self):
        if rank == 0:#{
            # kickstart pymultinest
            self.logger.info('starting pymultinest')
            # make a new directory for storing chains
            if not os.path.exists('chains'):
                os.mkdir('chains')
            # run pymultinest
            pymultinest.run(self._multinest_likelihood,
                            self.prior,
                            len(self.active_variables),
                            **self.pymultinest_parameters)
            self.logger.info('pymultinest finished')
            # send DIE_TAG to all salves
            for i in xrange(1, size):#{
                self.logger.debug('sending DIE_TAG to slave %i' % i)
                comm.send(None, dest=i, tag=DIE_TAG)
            #}
        #}
        else:
            # let slaves listen for likelihood evaluations
            self._listen_for_likelihood_calls()

    def _listen_for_likelihood_calls(self):
        status = MPI.Status()
        while True:#{
            cube = comm.recv(source=0, tag=MPI.ANY_TAG, status=status)
            if status == DIE_TAG:#{
                self.logger.debug('received DIE_TAG from master')
                break
            #} cube is sent in _multinest_likelihood
            self.logger.debug('received cube from master')
            # invoke _core_likelihood on slaves
            self._core_likelihood(cube)
        #}

    # invoked in __call__
    # defining the method of calculating likelihood value
    def _multinest_likelihood(self, cube, ndim, nparams):
        cube_content = np.empty(ndim)
        for i in xrange(ndim):
            cube_content[i] = cube[i]
        # heuristic for minimizers:
        # if a parameter value from outside of the cube is requested, return
        # the worst possible likelihood value
        if np.any(cube_content > 1.) or np.any(cube_content < 0.):#{
            self.logger.info('cube %s requested. returned most negative possible number' % cube_content)
            return np.nan_to_num(-np.inf)
        #}
        if rank != 0:#{
            raise RuntimeError('_multinest_likelihood must only be called on master')
        #}
        '''
        # this is just while True?
        error_count = 0
        while error_count < 5:
        '''
        while True:
            # sending cube to all slaves
            self.logger.debug('sent multinest-cube to slaves')
            for i in xrange(1, size):
                comm.send(cube_content, dest=i, tag=WORK_TAG)
            # invoke _core_likelihood on master
            likelihood = self._core_likelihood(cube_content)
            # check likelihood value until negative
            # or directly break out
            if likelihood < self.likelihood_threshold or not self.check_threshold:
                break
            else:
                self.logger.error('positive log-likelihood value encountered! redoing calculation')
        #}
        return likelihood
    
    def _core_likelihood(self, cube):
        self.logger.debug('beginning Likelihood-calculation for %s' % str(cube))
        # translate cube to variables for each field
        head_idx = 0
        tail_idx = 0
        multi_field = {}
        for ff in self.field_factory:#{
            ff_variables = {}
            tail_idx = head_idx + len(ff.active_variables)
            ff_cube = cube[head_idx:tail_idx]
            for i,av in enumerate(ff.active_variables):
                ff_variables[av] = ff_cube[i]
            self.logger.debug('creating '+ff.name+' field')
            multi_field[ff.name] = ff.generate(variables=ff_variables,
                                               ensemble_size=self.ensemble_size,
                                               random_seed=self.fixed_random_seed)
            head_idx += tail_idx
        #}
        assert(head_idx == tail_idx)
        assert(head_idx == len(self.active_variables))
        # create observables
        self.logger.debug('creating observables')
        observables = self.simulator(self.likelihood.observable_names,
                                     multi_field)
        # add up individual log-likelihood terms
        self.logger.debug('evaluating likelihood(s).')
        current_likelihood = self.likelihood(observables)
        self.logger.info('evaluated likelihood: %f for %s' % (current_likelihood, str(cube)))
        '''
        sample_callback relic
        '''
        return current_likelihood * self.likelihood_rescaler

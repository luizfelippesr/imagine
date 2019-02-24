'''
field class register the full default parameter value
which is passed down from field factory
and prepare to hand in to simulators the default checklist is a dict

members:
.field_checklist
    -- dict, with parameter name as entry, parameter xml path as content
    defines the parameters to be checked out by simulator
    should be fixed upon class designing
    
'''

import logging as log

class GeneralField(object):

    '''
    parameters
        -- dict of full parameter set {name: value}
    ensemble_size
        -- number of realisations in field ensemble
        useful only when random field is active
    random_seed
        -- random seed for generating random field realisations (likely in simulators)
        useful only when random field is active
    '''
    def __init__(self, parameters=dict(), ensemble_size=1, random_seed=None):
        self.name = 'general'
        self.parameters = parameters
        self.ensemble_size = ensemble_size
        self.random_seed = random_seed
        # if checklist has 'random_seed' entry
        if 'random_seed' in self.field_checklist.keys():
            self.parameters.update({'random_seed':self.random_seed})
            log.debug('update field random seed %s' % str(self.random_seed))

    @property
    def name(self):
        return self._name

    @name.setter
    def name(self, name):
        assert isinstance(name, str)
        self._name = name
    
    @property
    def field_checklist(self):
        return dict()

    @property
    def ensemble_size(self):
        return self._ensemble_size

    @ensemble_size.setter
    def ensemble_size(self, ensemble_size):
        assert (ensemble_size>0)
        self._ensemble_size = round(ensemble_size)
        log.debug('set field ensemble size %s' % str(ensemble_size))

    @property
    def random_seed(self):
        return self._random_seed

    @random_seed.setter
    def random_seed(self, random_seed):
        if random_seed is None:
            self._random_seed = int(0)
        else:
            self._random_seed = round(random_seed)

    @property
    def parameters(self):
        return self._parameters

    @parameters.setter
    def parameters(self, parameters):
        for k in parameters:
            assert (k in self.field_checklist.keys())
        try:
            self._parameters
            self._parameters.update(parameters)
            log.debug('update full parameters %s' % str(parameters))
        except AttributeError:
            self._parameters = parameters
            log.debug('set full parameters %s' % str(parameters))
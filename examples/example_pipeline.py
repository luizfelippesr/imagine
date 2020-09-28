#!/usr/env python

# Built-in imports
import os
import logging
from mpi4py import MPI
# External packages
import numpy as np
import healpy as hp
import astropy.units as u
import corner
import matplotlib
import matplotlib.pyplot as plt
# IMAGINE
import imagine as img
import imagine.observables as img_obs
## WMAP field factories
from imagine.fields.hamx import BregLSA, BregLSAFactory
from imagine.fields.hamx import TEregYMW16, TEregYMW16Factory
from imagine.fields.hamx import CREAna, CREAnaFactory
from imagine.fields.hamx import BrndES, BrndESFactory

matplotlib.use('Agg')
# Sets up MPI variables
comm = MPI.COMM_WORLD
mpirank = comm.Get_rank()
mpisize = comm.Get_size()

def prepare_mock_obs_data(b0=3, psi0=27, rms=4, err=0.01):
    """
    Prepares fake total intensity and Faraday depth data

    Parameters
    ----------
    b0, psi0 : float
        "True values" in the WMAP model

    Returns
    -------
    mock_data : imagine.observables.observables_dict.Measurements
        Mock Measurements
    mock_cov :  imagine.observables.observables_dict.Covariances
        Mock Covariances
    """
    ## Sets the resolution
    nside=2
    size = 12*nside**2

    # Generates the fake datasets
    sync_dset = img_obs.SynchrotronHEALPixDataset(data=np.empty(size)*u.K,
                                                  frequency=23*u.GHz, typ='I')
    fd_dset = img_obs.FaradayDepthHEALPixDataset(data=np.empty(size)*u.rad/u.m**2)

    # Appends them to an Observables Dictionary
    trigger = img_obs.Measurements()
    trigger.append(dataset=sync_dset)
    trigger.append(dataset=fd_dset)

    # Prepares the Hammurabi simmulator for the mock generation
    mock_generator = img.simulators.Hammurabi(measurements=trigger)

    # BregLSA field
    breg_lsa = BregLSA(parameters={'b0': b0, 'psi0': psi0, 'psi1': 0.9, 'chi0': 25.0})
    # CREAna field
    cre_ana = CREAna(parameters={'alpha': 3.0, 'beta': 0.0, 'theta': 0.0,
                                 'r0': 5.0, 'z0': 1.0,
                                 'E0': 20.6, 'j0': 0.0217})
    # TEregYMW16 field
    tereg_ymw16 = TEregYMW16(parameters={})
    ## Random field
    brnd_es = BrndES(parameters={'rms': rms, 'k0': 0.5, 'a0': 1.7,
                                 'k1': 0.5, 'a1': 0.0,
                                 'rho': 0.5, 'r0': 8., 'z0': 1.},
                     grid_nx=50, grid_ny=50, grid_nz=30)

    ## Generate mock data (run hammurabi)
    outputs = mock_generator([breg_lsa, brnd_es, cre_ana, tereg_ymw16])

    ## Collect the outputs
    mockedI = outputs[('sync', 23., nside, 'I')].global_data[0]
    mockedRM = outputs[('fd', None, nside, None)].global_data[0]
    dm=np.mean(mockedI)
    dv=np.std(mockedI)

    ## Add some noise that's just proportional to the average sync I by the factor err
    dataI = (mockedI + np.random.normal(loc=0, scale=err*dm, size=size)) << u.K
    errorI = ((err*dm)**2) << u.K
    sync_dset = img_obs.SynchrotronHEALPixDataset(data=dataI, error=errorI,
                                                  frequency=23*u.GHz, typ='I')
    ## Just 0.01*50 rad/m^2 of error for noise.
    dataRM = (mockedRM + np.random.normal(loc=0, scale=err*50,
                                          size=12*nside**2))*u.rad/u.m/u.m
    errorRM = ((err*50.)**2) << u.rad/u.m**2
    fd_dset = img_obs.FaradayDepthHEALPixDataset(data=dataRM, error=errorRM)

    mock_data = img_obs.Measurements()
    mock_data.append(dataset=sync_dset)
    mock_data.append(dataset=fd_dset)

    mock_cov = img_obs.Covariances()
    mock_cov.append(dataset=sync_dset)
    mock_cov.append(dataset=fd_dset)

    return mock_data, mock_cov


def example_run(pipeline_class=img.pipelines.MultinestPipeline,
                sampling_controllers={}, ensemble_size=8,
                run_directory='example_pipeline',
                n_evals_report = 50,
                true_pars={'b0': 3, 'psi0': 27, 'rms': 4},
                obs_err=0.01):

    # Creates run directory for storing the chains and log
    if mpirank==0:
        os.makedirs(run_directory, exist_ok=True)
    comm.Barrier()

    # Sets up logging
    logging.basicConfig(
      filename=os.path.join(run_directory, 'example_pipeline.log'),
      level=logging.INFO)

    # Creates the mock dataset based on "true" parameters provided
    if mpirank==0:
        print('\nGenerating mock data', flush=True)
    mock_data, mock_cov = prepare_mock_obs_data(err=obs_err, **true_pars)
    if mpirank==0:
        print('\nPreparing pipeline', flush=True)

    # Setting up of the pipeline
    ## Use an ensemble to estimate the galactic variance
    likelihood = img.likelihoods.EnsembleLikelihood(mock_data, mock_cov)

    ## WMAP B-field, vary only b0 and psi0
    breg_factory = BregLSAFactory()
    breg_factory.priors = {'b0':  img.priors.FlatPrior(xmin=2., xmax=8.),
                           'psi0': img.priors.FlatPrior(xmin=0., xmax=50.)}
    breg_factory.active_parameters = ('b0', 'psi0')
    ## Random B-field, vary only RMS amplitude
    brnd_factory = BrndESFactory(grid_nx=25, grid_ny=25, grid_nz=15)
    # Note that the random grid resolution is lower than the one
    # used for the mock, reflecting the typical case
    brnd_factory.active_parameters = ('rms',)
    brnd_factory.priors = {'rms': img.priors.FlatPrior(xmin=2., xmax=8.)}
    ## Fixed CR model
    cre_factory = CREAnaFactory()
    ## Fixed FE model
    fereg_factory = TEregYMW16Factory()

    # Final Field factory list
    factory_list = [breg_factory, brnd_factory, cre_factory, fereg_factory]

    # Prepares simulator
    simulator = img.simulators.Hammurabi(measurements=mock_data)

    # Prepares pipeline
    pipeline = pipeline_class(simulator=simulator,
                              show_progress_reports=True,
                              factory_list=factory_list,
                              n_evals_report = n_evals_report,
                              likelihood=likelihood,
                              ensemble_size=ensemble_size,
                              run_directory=run_directory)
    pipeline.sampling_controllers = sampling_controllers

    timer = img.tools.Timer()
    # Checks the runtime
    timer.tick('likelihood')
    pipeline._likelihood_function([3,3,3])
    test_time = timer.tock('likelihood')
    if mpirank == 0:
        print('\nSingle likelihood evaluation: {0:.2f} s'.format(test_time))
    comm.Barrier()

    # Runs!
    if mpirank == 0:
        print('\n\nRunning the pipeline\n',flush=True)
    timer.tick('pipeline')
    results=pipeline()
    total_time = timer.tock('pipeline')
    if mpirank == 0:
        print('\n\nFinished the run in {0:.2f}'.format(total_time), flush=True)
    comm.Barrier()

    if mpirank == 0:
        # Reports the evidence (to file)
        report_file=os.join(run_directory,
                            'example_pipeline_results.txt')
        with open(report_file, 'w+') as f:
            f.write('log evidence: {}'.format( pipeline.log_evidence))
            f.write('log evidence error: {}'.format(pipeline.log_evidence_err))

        # Reports the posterior
        f = pipeline.corner_plot(truths_dict={'breg_lsa_b0': true_pars['b0'],
                                              'breg_lsa_psi0': true_pars['psi0'],
                                              'breg_wmap_rms': true_pars['rms']})

        f.savefig(os.path.join(run_directory,'corner_plot_truth.pdf'))
        # Prints setup
        print('\nRC used:', img.rc)
        print('Seed used:', pipeline.master_seed)
        # Prints some results
        print('\nEvidence found:', pipeline.log_evidence, '±', pipeline.log_evidence_err)
        print('\nParameters summary:')
        for parameter in pipeline.active_parameters:
            print(parameter)
            constraints = pipeline.posterior_summary[parameter]
            for k in ['median','errup','errlo']:
                print('\t', k, constraints[k])


os.environ['OMP_NUM_THREADS'] = '4'
if __name__ == '__main__':
    if mpirank == 0:
        print('Warning, this example is still under development!')

    # Sets run directory name
    run_directory=os.path.join('runs','example_pipeline')
    # Starts the run
    example_run(sampling_controllers={'max_iter': 1, 'n_live_points':40},
                run_directory=run_directory)

"""
For convenience we define dictionary of Observable objects as
ObservableDict from which one can define define the classes Measurements,
Covariances, Simulations and Masks, which can be used to store:

    * measured data sets
    * measured/simulated covariances
    * simulated ensemble sets
    * mask "maps" (but actually mask lists)


Conventions for observables entries
    * **Faraday depth:** `('fd','nan',str(size/Nside),'nan')`
    * **Dispersion measure**: `('dm','nan',str(pix),'nan')`
    * **Synchrotron emission**: `('sync',str(freq),str(pix),X)`
        where X stands for:
            * 'I' - total intensity (in unit K-cmb)
            * 'Q' - Stokes Q (in unit K-cmb, IAU convention)
            * 'U' - Stokes U (in unit K-cmb, IAU convention)
            * 'PI' - polarisation intensity (in unit K-cmb)
            * 'PA' - polarisation angle (in unit rad, IAU convention)

    Remarks:
        * `str(freq)`, polarisation-related-flag are redundant for Faraday depth
            and dispersion measure so we put 'nan' instead
        * `str(pix/nside)` stores either Healpix Nside, or just number of
            pixels/points we do this for flexibility, in case users have non-HEALPix
            based in/output

Remarks on the observable tags
    str(freq), polarisation-related-flag are redundant for Faraday depth and dispersion measure so we put 'nan' instead

    str(pix/nside) stores either Healpix Nisde, or just number of pixels/points
    we do this for flexibility, in case users have non-HEALPix-map-like in/output

Masking convention
    masked erea associated with pixel value 0,
    unmasked area with pixel value 1

Masking method
    mask only applies to observables/covariances
    observable after masking will be re-recorded as plain data type


.. note:: Distribution with MPI

    * all data are either distributed or copied, where "copied" means each node
      stores the identical copy which is convenient for hosting measured data
      and mask map

    * Covariances has Field object with global shape "around"
      (data_size//mpisize, data_size) "around" means to distribute matrix
      correctly as described in "imagine/tools/mpi_helper.py"
"""

# %% IMPORTS
# Built-in imports
import abc
import logging as log

# Package imports
import numpy as np

# IMAGINE imports
from imagine.observables.dataset import Dataset, HEALPixDataset
from imagine.observables.observable import Observable
from imagine.tools import BaseClass, mask_cov, mask_obs, req_attr

# All declaration
__all__ = ['ObservableDict', 'Masks', 'Measurements', 'Simulations',
           'Covariances']


# %% CLASS DEFINITIONS
class ObservableDict(BaseClass, metaclass=abc.ABCMeta):
    """
    Base class from which `imagine.observables.observable_dict.Measurements`,
    `imagine.observables.observable_dict.Covariances`, `imagine.observables.observable_dict.Simulations`
    and `imagine.observables.observable_dict.Masks` are derived.

    See `imagine.observables.observable_dict` module documentation for
    further details.
    """
    def __init__(self):
        # Call super constructor
        super().__init__()

        self._archive = {}

    @property
    def archive(self):
        return self._archive

    def keys(self):
        return self._archive.keys()

    def __getitem__(self, key):
        return self._archive[key]

    @abc.abstractmethod
    def append(self, name, new_data, plain=False):
        """
        Adds/updates name and data

        Parameters
        ----------
        name : str tuple
            Should follow the convention:
            ``(data-name,str(data-freq),str(data-Nside/size),str(ext))``.
            If data is independent from frequency, set 'nan'.
            `ext` can be 'I','Q','U','PI','PA', 'nan' or other customized tags.
        new_data
            distributed/copied :py:class:`numpy.ndarray` or :py:class:`Observable <imagine.observables.observable.Observable>`
        plain : bool
            If True, means unstructured data.
            If False (default case), means HEALPix-like sky map.
        """
        raise NotImplementedError



class Masks(ObservableDict):
    """
    Stores HEALPix mask maps.

    After the Masks dictionary is assembled it can be applied to any other
    observables dictionary to return a dictionary containing masked maps
    (see below).


    Example
    --------
    >>> import imagine.observables as obs
    >>> import numpy as np
    >>> meas, cov, mask = obs.Measurements(), obs.Covariances(), obs.Masks()
    >>> key = ('test','nan','4','nan')
    >>> meas.append(('test','nan','4','nan'), np.array([[1,2,3,4.]]), plain=True)
    >>> mask.append(('test','nan','4','nan'), np.array([[1,1,0,0.]]), plain=True)
    >>> masked_meas = mask(meas)
    >>> print(masked_meas[('test','nan','2','nan')].data)
    [[1. 2.]]
    >>> cov.append(('test','nan','4','nan'), np.diag((1,2,3,4.)), plain=True)
    >>> masked_cov = mask(cov)
    >>> print(masked_cov[('test','nan','2','nan')].data)
    [[1. 0.]
     [0. 2.]]


    """

    def append(self, name, new_data, plain=False):
        """
        Adds/updates name and data

        Parameters
        ----------
        name : str tuple
            Should follow the convention:
            ``(data-name,str(data-freq),str(data-Nside/"tab"),str(ext))``.
            If data is independent from frequency, set 'nan'.
            `ext` can be 'I','Q','U','PI','PA', 'nan' or other customized tags.
        new_data
            distributed/copied :py:class:`numpy.ndarray` or :py:class:`Observable <imagine.observables.observable.Observable>`
        plain : bool
            If True, means unstructured data.
            If False (default case), means HEALPix-like sky map.
        """
        log.debug('@ observable_dict::Masks::append')
        assert (len(name) == 4)
        if isinstance(new_data, Observable):
            assert (new_data.dtype == 'measured')
            if not plain:
                assert (new_data.size == 12*np.uint(name[2])**2)
            self._archive.update({name: new_data})
        elif isinstance(new_data, np.ndarray):
            assert (new_data.shape[0] == 1)
            if not plain:
                assert (new_data.shape[1] == _Nside_to_Npixels(name[2]))
            self._archive.update({name: Observable(new_data, 'measured')})
        else:
            raise TypeError('unsupported data type')

    def __call__(self, observable_dict):
        """
        Applies the masks

        Parameters
        ----------
        observable_dict : imagine.observables.ObservableDict
            Dictionary containing (some) entries where one wants to apply the
            masks.

        Returns
        -------
        masked_dict : imagine.observables.ObservableDict
            New observables dictionary containing masked entries (any entries
            in the original dictionary for which no mask was specified are
            referenced in `masked_dict` without modification).
        """
        assert (isinstance(observable_dict, Measurements) or
                isinstance(observable_dict, Simulations) or
                isinstance(observable_dict, Covariances))

        # Creates an empty ObservableDict of the same type/subclass
        masked_dict = type(observable_dict)()

        for name, observable in observable_dict._archive.items():
            if name not in self._archive:
                # Saves reference to any observables where the masks are
                # not available
                masked_dict.append(name, observable)
            else:
                # Reads the mask
                mask = self._archive[name].data
                # Applies appropriate function
                if isinstance(observable_dict, Covariances):
                    masked_data = mask_cov(observable_dict[name].data, mask)
                else:
                    masked_data = mask_obs(observable_dict[name].data, mask)

                # Appends the masked Observable, copying (refs to) units/coords
                new_name = (name[0], name[1], str(masked_data.shape[1]), name[3])
                masked_dict.append(new_name, masked_data, plain=True)
                masked_dict.coords = observable.coords
                masked_dict.unit = observable.unit
        return masked_dict


class Measurements(ObservableDict):
    """
    Stores observational data sets

    See `imagine.observables.observable_dict` module documentation for
    further details.
    """

    def append(self, name=None, new_data=None, plain=False, dataset=None):
        """
        Adds/updates name and data

        Parameters
        ----------
        dataset : imagine.observables.dataset.Dataset
            The IMAGINE dataset already adjusts the format of the data and sets the
            adequate key. If `dataset` is present, all other arguments will be ignored.
        name : str tuple
            Should follow the convention:
            ``(data-name,str(data-freq),str(data-Nside)/"tab",str(ext))``.
            If data is independent from frequency, set 'nan'.
            `ext` can be 'I','Q','U','PI','PA', 'nan' or other customized tags.
        new_data : numpy.ndarray or imagine.observables.observable.Observable
            distributed/copied :py:class:`numpy.ndarray` or :py:class:`Observable <imagine.observables.observable.Observable>`
        plain : bool
            If True, means unstructured/tabular data.
            If False (default case), means HEALPix-like sky map.
        """
        log.debug('@ observable_dict::Measurements::append')

        if dataset is not None:
            assert isinstance(dataset, Dataset)
            name = dataset.key
            new_data = dataset.data
            coords = dataset.coords
            if isinstance(dataset, HEALPixDataset):
                plain=False
            else:
                plain=True
        else:
            coords=None

        assert (len(name) == 4), 'Wrong format for Observable key!'
        if isinstance(new_data, Observable):
            assert (new_data.dtype == 'measured')
            self._archive.update({name: new_data})
        elif isinstance(new_data, np.ndarray):
            if not plain:
                assert (new_data.shape[1] == _Nside_to_Npixels(name[2]))
            self._archive.update({name: Observable(data=new_data,
                                                   dtype='measured',
                                                   coords=coords)})
        else:
            raise TypeError('Unsupported data type')


class Simulations(ObservableDict):
    """
    Stores simulated ensemble sets

    See `imagine.observables.observable_dict` module documentation for
    further details.
    """

    def append(self, name, new_data, plain=False):
        """
        Adds/updates name and data

        Parameters
        ----------
        name : str tuple
            Should follow the convention:
            ``(data-name,str(data-freq),str(data-Nside)/"tab",str(ext))``.
            If data is independent from frequency, set 'nan'.
            `ext` can be 'I','Q','U','PI','PA', 'nan' or other customized tags.
        new_data
            distributed/copied :py:class:`numpy.ndarray` or :py:class:`Observable <imagine.observables.observable.Observable>`
        plain : bool
            If True, means unstructured data.
            If False (default case), means HEALPix-like sky map.
        """
        log.debug('@ observable_dict::Simulations::append')

        assert (len(name) == 4)
        if name in self._archive.keys():  # app
            self._archive[name].rw_flag = False
            self._archive[name].append(new_data)
        else:  # new_data
            if isinstance(new_data, Observable):
                self._archive.update({name: new_data})
            elif isinstance(new_data, np.ndarray):  # distributed data
                self._archive.update({name: Observable(new_data, 'simulated')})
            else:
                raise TypeError('unsupported data type')



class Covariances(ObservableDict):
    """
    Stores observational covariances

    See `imagine.observables.observable_dict` module documentation for
    further details.
    """

    def append(self, name=None, new_data=None, plain=False, dataset=None):
        """
        Adds/updates name and data

        Parameters
        ----------
        name : str tuple
            Should follow the convention:
            ``(data-name,str(data-freq),str(data-Nside)/"tab",str(ext))``.
            If data is independent from frequency, set 'nan'.
            `ext` can be 'I','Q','U','PI','PA', 'nan' or other customized tags.
        data
            distributed/copied ndarray/Observable
        plain : bool
            If True, means unstructured data.
            If False (default case), means HEALPix-like sky map.
        """
        log.debug('@ observable_dict::Covariances::append')

        if dataset is not None:
            assert isinstance(dataset, Dataset)
            name = dataset.key
            new_data = dataset.cov

            if isinstance(dataset, HEALPixDataset):
                plain=False
            else:
                plain=True

        assert (len(name) == 4)
        if isinstance(new_data, Observable):  # always rewrite
            self._archive.update({name: new_data})  # rw
        elif isinstance(new_data, np.ndarray):
            if not plain:
                assert (new_data.shape[1] == _Nside_to_Npixels(name[2]))
            self._archive.update({name: Observable(new_data, 'covariance')})
        else:
            raise TypeError('unsupported data type')


def _Nside_to_Npixels(Nside):
    return 12*int(Nside)**2

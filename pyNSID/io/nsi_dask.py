# starting code from
# https://scikit-allel.readthedocs.io/en/v0.21.1/_modules/allel/model/dask.html

import numpy as np
import string

import dask.array as da

import os
import sys
from warnings import warn
import h5py

from .hdf_utils import check_if_main, get_attr, create_results_group, link_as_main, write_main_dataset, copy_attributes
from .write_utils import  Dimension


def get_chunks(data, chunks=None):
    """Try to guess a reasonable chunk shape to use for block-wise
    algorithms operating over `data`."""

    if chunks is None:

        if hasattr(data, 'chunklen') and hasattr(data, 'shape'):
            # bcolz carray, chunk first dimension only
            return (data.chunklen,) + data.shape[1:]

        elif hasattr(data, 'chunks') and hasattr(data, 'shape') and \
                len(data.chunks) == len(data.shape):
            # h5py dataset
            return data.chunks

        else:
            # fall back to something simple, ~1Mb chunks of first dimension
            row = np.asarray(data[0])
            chunklen = max(1, (2**20) // row.nbytes)
            if row.shape:
                chunks = (chunklen,) + row.shape
            else:
                chunks = (chunklen,)
            return chunks

    else:

        return chunks


def ensure_array_like(data):
    if not hasattr(data, 'shape') or not hasattr(data, 'dtype'):
        a = np.asarray(data)
        if len(a.shape) == 0:
            raise ValueError('not array-like')
        return a
    else:
        return data


def ensure_dask_array(data, chunks=None):
    if isinstance(data, da.Array):
        if chunks:
            data = data.rechunk(chunks)
        return data
    else:
        data = ensure_array_like(data)
        chunks = get_chunks(data, chunks)
        return da.from_array(data, chunks=chunks)


def view_subclass(darr, cls):
    """View a dask Array as an instance of a dask Array sub-class."""
    return cls(darr.dask, name=darr.name, chunks=darr.chunks,
               dtype=darr.dtype, shape=darr.shape)


class NSIDask(da.Array):
    """Dask NSI data array.

    To instantiate from an existing array-like object,
    use :func:`NSIDask.from_array` - requires numpy array
    or  :func:`NSIDask.from_hdf5`  - requires NSID Dataset

    This dask array is extended to have the following attributes:
    -data_type: str ('image', 'image_stack',  spectrum_image', ...
    -units: str
    -title: name of the data set
    -modality
    -source
    -axes: dictionary of NSID Dimensions one for each data dimension
                    (the axes are dimension datsets with name, label, units, and 'dimension_type' attributes).

    -attrs: dictionary of additional metadata
    -orginal_metadata: dictionary of original metadata of file,

    -labels: returns labels of all dimensions.

    functions:
    set_dimension(axis, dimensions): set a NSID Dimension to a specific axis
    """

    def __init__(self, *args, **kwargs):
        super(NSIDask, self).__init__()


    @classmethod
    def from_array(cls, x, chunks=None, name=None, lock=False):
        # override this as a class method to allow sub-classes to return
        # instances of themselves

        # ensure array-like
        x = ensure_array_like(x)
        if hasattr(cls, 'check_input_data'):
            cls.check_input_data(x)

        # determine chunks, guessing something reasonable if user does not
        # specify
        chunks = get_chunks(np.array(x), chunks)

        # create vanilla dask array
        darr = da.from_array(np.array(x), chunks=chunks, name=name, lock=lock)

        # view as sub-class
        cls = view_subclass(darr, cls)
        cls.data_type = 'generic'
        cls.units = ''
        cls.title = ''
        cls.quantity = 'generic'

        cls.modality = ''
        cls.source = ''
        cls.data_descriptor = ''

        cls.axes = {}
        for dim in range(cls.ndim):
            # TODO: add parent to dimension to set attribute if name changes
            cls.labels.append(string.ascii_lowercase[dim])
            cls.set_dimension(dim, Dimension(string.ascii_lowercase[dim], np.arange(cls.shape[dim]), 'generic',
                                                   'generic', 'generic'))
        cls.attrs = {}
        cls.group_attrs = {}
        cls.original_metadata = {}
        return cls

    @classmethod
    def from_hdf5(cls, dset, chunks=None, name=None, lock=False):

        # determine chunks, guessing something reasonable if user does not
        # specify
        chunks = get_chunks(np.array(dset), chunks)

        # create vanilla dask array
        darr = da.from_array(np.array(dset), chunks=chunks, name=name, lock=lock)

        # view as sub-class
        cls = view_subclass(darr, cls)

        if 'title' in dset.attrs:
            cls.title = dset.attrs['title']
        else:
            cls.title = dset.name

        if 'units' in dset.attrs:
            cls.units = dset.attrs['units']
        else:
            cls.units = 'generic'

        if 'quantity' in dset.attrs:
            cls.quantity = dset.attrs['quantity']
        else:
            cls.quantity = 'generic'

        if 'data_type' in dset.attrs:
            cls.data_type = dset.attrs['data_type']
        else:
            cls.data_type = 'generic'

        #TODO: mdoality and source not yet properties
        if 'modality' in dset.attrs:
            cls.modality = dset.attrs['modality']
        else:
            cls.modality = 'generic'

        if 'source' in dset.attrs:
            cls.source = dset.attrs['source']
        else:
            cls.source = 'generic'

        cls.axes ={}
        for dim in range(dset.ndim):
            cls.set_dimension(dim, Dimension(dset.dims[dim].label, np.array(dset.dims[dim][0]),
                                                 dset.axes_quantities[dim], dset.axes_units[dim],
                                                 dset.dimension_types[dim]))
        cls.attrs = dict(dset.attrs)

        return cls

    def to_hdf5(self, h5_group):
        if  self.title.strip() == '':
            main_data_name = 'nDim_Data'
        else:
            main_data_name = self.title


        dset = write_main_dataset(h5_group, np.array(self), main_data_name,
                                 self.quantity, self.units, self.data_type, self.modality,
                                 self.source, self.axes, verbose=False)
        for key, item in self.attrs.items():
            #TODO: Check item to be simple
            dset.attrs[key] = item

        for key, item in self.original_metadata.items():
            dset.parents['original_metadata'] = []
            dset.parents['original_metadata'].attrs[key] = item

    def set_dimension(self,dim, dimension):
        #TODO: Check whether dimension valid
        setattr(self, dimension.name,dimension)
        setattr(self, f'dim_{dim}', dimension)

        self.axes[dim] = dimension

    def get_extent(self, dimensions):
        """
        get image extend as neeed i.e. in matplotlib's imshow function. This function works for equi or non-equi spaced axes
        """
        extend = []
        for i, dim in enumerate(dimensions):
            start = self.axes[dim].values[0] - (self.axes[dim].values[1] -self.axes[dim].values[0])/2
            end = self.axes[dim].values[-1]  - (self.axes[dim].values[-1]-self.axes[dim].values[-2])/2
            if i == 1:
                extend.append(end) #y axis starts on top
                extend.append(start)
            else:
                extend.append(start)
                extend.append(end)
        return extend


    @property
    def labels(self):
        labels = []
        for key, dim in self.axes.items():
            labels.append(dim.name)
        return labels

    @property
    def title(self):
        return self._title
    @title.setter
    def title(self, value):
        if isinstance(value, str):
            self._title = value
        else:
            raise ValueError('title needs to be a string')

    @property
    def units(self):
        return self._units
    @units.setter
    def units(self, value):
        if isinstance(value, str) :
            self._units = value
        else:
            raise ValueError('units needs to be a string')


    @property
    def data_descriptor(self):
        return self._data_descriptor
    @data_descriptor.setter
    def data_descriptor(self, value):
        if isinstance(value, str):
            self._data_descriptor = value
        else:
            raise ValueError('data_descriptor needs to be a string')

    @property
    def data_type(self):
        return self._data_type
    @data_type.setter
    def data_type(self, value):
        if isinstance(value, str):
            self._data_type = value
        else:
            raise ValueError('data_type needs to be a string')
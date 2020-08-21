# starting code from
# https://scikit-allel.readthedocs.io/en/v0.21.1/_modules/allel/model/dask.html

import numpy as np
import string

import dask.array as da

import os, sys
sys.path.append('../../../../sidpy/')
import sidpy as sid


from warnings import warn
import h5py

from .hdf_utils import check_if_main, get_attr, create_results_group, link_as_main, write_main_dataset, copy_attributes


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



def from_hdf5(cls, dset, chunks=None, name=None, lock=False):

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

    for dim in range(np.array(dset).ndim):
        #print(dim, dset.dims[dim].label)
        #print(dset.dims[dim][0][0])
        dim_dict = dict(dset.parent[dset.dims[dim].label].attrs)
        #print(dset.dims[dim].label, np.array(dset.dims[dim][0]))
        #print(dset.parent[dset.dims[0].label][()])
        #print(dim_dict['quantity'], dim_dict['units'], dim_dict['dimension_type'])
        cls.set_dimension(dim, sid.sid.Dimension(dset.dims[dim].label, np.array(dset.parent[dset.dims[dim].label][()]),
                                                dim_dict['quantity'], dim_dict['units'],
                                                dim_dict['dimension_type']))
    cls.attrs = dict(dset.attrs)

    cls.original_metadata = {}
    if 'original_metadata' in dset.parent:
        cls.original_metadata = dict(dset.parent['original_metadata'].attrs)


    return cls

    def to_hdf5(self, h5_group):
        if  self.title.strip() == '':
            main_data_name = 'nDim_Data'
        else:
            main_data_name = self.title
        print(h5_group)
        print(h5_group.keys())

        print(main_data_name)

        dset = write_main_dataset(h5_group, np.array(self), main_data_name,
                                 self.quantity, self.units, self.data_type, self.modality,
                                 self.source, self.axes, verbose=False)
        print('d',dset)

        for key, item in self.attrs.items():
            #TODO: Check item to be simple
            dset.attrs[key] = item

        original_group = h5_group.create_group('original_metadata')
        for key, item in self.original_metadata.items():
            original_group.attrs[key] = item

        if hasattr(self, 'aberrations'):
            aberrations_group = h5_group.create_group('aberrations')
            for key, item in self.aberrations.items():
                aberrations_group.attrs[key] = item

        if hasattr(self, 'annotations'):
            annotations_group = h5_group.create_group('annotations')
            for key, item in self.annotations.items():
                annotations_group.attrs[key] = item

    def copy(self):
        dset_copy = NSIDask.from_array(self, self.chunks, self.name)

        dset_copy.title = self.title
        dset_copy.units = self.units
        dset_copy.quantity = self.quantity
        dset_copy.data_type = self.data_type
        dset_copy.modality = self.modality
        dset_copy.source = self.source

        dset_copy.axes = {}
        for dim in range(dset_copy.ndim):
            dset_copy.set_dimension(dim, self.axes[dim].copy())
        dset_copy.attrs = dict(self.attrs).copy()

        return dset_copy

    def set_dimension(self,dim, dimension):
        #TODO: Check whether dimension valid
        setattr(self, dimension.name,dimension)
        setattr(self, 'dim_{}'.format(dim), dimension)

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
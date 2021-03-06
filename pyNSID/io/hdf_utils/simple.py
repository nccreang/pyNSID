# -*- coding: utf-8 -*-
"""
Lower-level and simpler NSID-specific HDF5 utilities that facilitate higher-level data operations
Created on Tue Nov  3 21:14:25 2015
@author: Suhas Somnath, Chris Smith
"""
from __future__ import division, print_function, absolute_import, unicode_literals
import collections
from warnings import warn
import sys
import h5py
import numpy as np
import dask.array as da

from sidpy.base.num_utils import contains_integers
from sidpy.base.string_utils import validate_single_string_arg, \
    clean_string_att, validate_list_of_strings
from sidpy.sid import Dimension
from sidpy.hdf.dtype_utils import validate_dtype
## Must be reimplemented
from sidpy.hdf.hdf_utils import get_auxiliary_datasets, link_h5_obj_as_alias, \
    get_attr, write_simple_attrs, lazy_load_array, \
    validate_h5_objs_in_same_h5_file

from .base import write_book_keeping_attrs
from ..dimension import validate_dimensions

if sys.version_info.major == 3:
    unicode = str
"""
__all__ = ['assign_group_index', 'check_and_link_ancillary', 'check_for_matching_attrs', 'check_for_old',
           'check_if_main', 'copy_attributes', 'copy_main_attributes']
"""


def get_all_main(parent, verbose=False):
    """
    Simple function to recursively print the contents of an hdf5 group
    Parameters
    ----------
    parent : :class:`h5py.Group`
        HDF5 Group to search within
    verbose : bool, optional. Default = False
        If true, extra print statements (usually for debugging) are enabled
    Returns
    -------
    main_list : list of h5py.Dataset
        The datasets found in the file that meet the 'Main Data' criteria.
    """
    if not isinstance(parent, (h5py.Group, h5py.File)):
        raise TypeError('parent should be a h5py.File or h5py.Group object')

    from ..nsi_data import NSIDataset

    main_list = list()

    def __check(name, obj):
        if verbose:
            print(name, obj)
        if isinstance(obj, h5py.Dataset):
            if verbose:
                print(name, 'is an HDF5 Dataset.')
            ismain = check_if_main(obj)
            if ismain:
                if verbose:
                    print(name, 'is a `Main` dataset.')
                main_list.append(NSIDataset(obj))

    if verbose:
        print('Checking the group {} for `Main` datasets.'.format(parent.name))
    parent.visititems(__check)

    return main_list

def find_dataset(h5_group, dset_name):
    """
    Uses visit() to find all datasets with the desired name
    Parameters
    ----------
    h5_group : :class:`h5py.Group`
        Group to search within for the Dataset
    dset_name : str
        Name of the dataset to search for
    Returns
    -------
    datasets : list
        List of [Name, object] pairs corresponding to datasets that match `ds_name`.
    """
    from ..nsi_data import NSIDataset

    if not isinstance(h5_group, (h5py.File, h5py.Group)):
        raise TypeError('h5_group should be a h5py.File or h5py.Group object')
    dset_name = validate_single_string_arg(dset_name, 'dset_name')
    
    # print 'Finding all instances of', ds_name
    datasets = []

    def __find_name(name, obj):
        if dset_name in name.split('/')[-1] and isinstance(obj, h5py.Dataset):
            try:
                datasets.append(NSIDataset(obj))
            except:# TypeError: #TODO: fix this 
                pass
                #datasets.append(obj)
        return

    h5_group.visititems(__find_name)

    return datasets


def find_results_groups(h5_main, tool_name, h5_parent_group=None):
    """
    Finds a list of all groups containing results of the process of name
    `tool_name` being applied to the dataset
    Parameters
    ----------
    h5_main : h5 dataset reference
        Reference to the target dataset to which the tool was applied
    tool_name : String / unicode
        Name of the tool applied to the target dataset
    h5_parent_group : h5py.Group, optional. Default = None
        Parent group under which the results group will be searched for. Use
        this option when the results groups are contained in different HDF5
        file compared to `h5_main`. BY default, this function will search
        within the same group that contains `h5_main`
    Returns
    -------
    groups : list of references to :class:`h5py.Group` objects
        groups whose name contains the tool name and the dataset name
    """
    if not isinstance(h5_main, h5py.Dataset):
        raise TypeError('h5_main should be a h5py.Dataset object')
    tool_name = validate_single_string_arg(tool_name, 'tool_name')

    if h5_parent_group is not None:
        if not isinstance(h5_parent_group, (h5py.File, h5py.Group)):
            raise TypeError("'h5_parent_group' should either be a h5py.File "
                            "or h5py.Group object")
    else:
        h5_parent_group = h5_main.parent

    dset_name = h5_main.name.split('/')[-1]
    groups = []
    for key in h5_parent_group.keys():
        if dset_name in key and tool_name in key and isinstance(h5_parent_group[key], h5py.Group):
            groups.append(h5_parent_group[key])
    return groups


def check_and_link_ancillary(h5_dset, anc_names, h5_main=None, anc_refs=None):
    """
    This function will add references to auxilliary datasets as attributes
    of an input dataset.
    If the entries in anc_refs are valid references, they will be added
    as attributes with the name taken from the corresponding entry in
    anc_names.
    If an entry in anc_refs is not a valid reference, the function will
    attempt to get the attribute with the same name from the h5_main
    dataset
    Parameters
    ----------
    h5_dset : HDF5 Dataset
        dataset to which the attributes will be written
    anc_names : list of str
        the attribute names to be used
    h5_main : HDF5 Dataset, optional
        dataset from which attributes will be copied if `anc_refs` is None
    anc_refs : list of HDF5 Object References, optional
        references that correspond to the strings in `anc_names`
    Returns
    -------
    None
    Notes
    -----
    Either `h5_main` or `anc_refs` MUST be provided and `anc_refs` has the
    higher priority if both are present.
    """
    if not isinstance(h5_dset, h5py.Dataset):
        raise TypeError('h5_dset should be a h5py.Dataset object')

    if isinstance(anc_names, (str, unicode)):
        anc_names = [anc_names]
    if isinstance(anc_refs, (h5py.Dataset, h5py.Group, h5py.File,
                             h5py.Reference)):
        anc_refs = [anc_refs]

    if not isinstance(anc_names, (list, tuple)):
        raise TypeError('anc_names should be a list / tuple')
    if h5_main is not None:
        if not isinstance(h5_main, h5py.Dataset):
            raise TypeError('h5_main should be a h5py.Dataset object')
        validate_h5_objs_in_same_h5_file(h5_dset, h5_main)
    if anc_refs is not None:
        if not isinstance(anc_refs, (list, tuple)):
            raise TypeError('anc_refs should be a list / tuple')

    if anc_refs is None and h5_main is None:
        raise ValueError('No objected provided to link as ancillary')

    def __check_and_link_single(h5_obj_ref, target_ref_name):
        if isinstance(h5_obj_ref, h5py.Reference):
            # TODO: Same HDF5 file?
            h5_dset.attrs[target_ref_name] = h5_obj_ref
        elif isinstance(h5_obj_ref, (h5py.Dataset, h5py.Group, h5py.File)):
            validate_h5_objs_in_same_h5_file(h5_obj_ref, h5_dset)
            h5_dset.attrs[target_ref_name] = h5_obj_ref.ref
        elif h5_main is not None:
            h5_anc = get_auxiliary_datasets(h5_main, aux_dset_name=[target_ref_name])
            if len(h5_anc) == 1:
                link_h5_obj_as_alias(h5_dset, h5_anc[0], target_ref_name)
        else:
            warnstring = '{} is not a valid h5py Reference and will be skipped.'.format(repr(h5_obj_ref))
            warn(warnstring)

    if bool(np.iterable(anc_refs) and not isinstance(anc_refs, h5py.Dataset)):
        """
        anc_refs can be iterated over
        """
        for ref_name, h5_ref in zip(anc_names, anc_refs):
            __check_and_link_single(h5_ref, ref_name)
    elif anc_refs is not None:
        """
        anc_refs is just a single value
        """
        __check_and_link_single(anc_refs, anc_names)
    elif isinstance(anc_names, str) or isinstance(anc_names, unicode):
        """
        Single name provided
        """
        __check_and_link_single(None, anc_names)
    else:
        """
        Iterable of names provided
        """
        for name in anc_names:
            __check_and_link_single(None, name)

    h5_dset.file.flush()


def copy_attributes(source, dest, skip_refs=True, verbose=False):
    """
    Copy attributes from one h5object to another
    Parameters
    ----------
    source : h5py.Dataset, :class:`h5py.Group`, or :class:`h5py.File`
        Object containing the desired attributes
    dest : h5py.Dataset, :class:`h5py.Group`, or :class:`h5py.File`
        Object to which the attributes need to be copied to
    skip_refs : bool, optional. default = True
        Whether or not the references (dataset and region) should be skipped
    verbose : bool, optional. Defualt = False
        Whether or not to print logs for debugging
    """
    mesg = 'should be a h5py.Dataset, h5py.Group,or h5py.File object'
    if not isinstance(source, (h5py.Dataset, h5py.Group, h5py.File)):
        raise TypeError('source ' + mesg)
    if not isinstance(dest, (h5py.Dataset, h5py.Group, h5py.File)):
        raise TypeError('dest ' + mesg)

    skip_dset_refs = skip_refs
    try:
        validate_h5_objs_in_same_h5_file(source, dest)
    except ValueError:
        if not skip_refs:
            warn('Dataset references will not be copied since {} and {} are '
                 'in different files'.format(source, dest))
        skip_dset_refs = True

    for att_name in source.attrs.keys():
        print(att_name)
        if att_name not in ['DIMENSION_LIST']:
            att_val = get_attr(source, att_name)
        """
        Don't copy references unless asked
        """
        if isinstance(att_val, h5py.Reference) and not isinstance(att_val, h5py.RegionReference):
            if not skip_dset_refs:
                if verbose:
                    print('dset ref copying ' + att_name)
                dest.attrs[att_name] = att_val
        elif isinstance(att_val, h5py.RegionReference):
            # handled in dedicated if condition below
            continue
        else:
            # everything else
            if verbose:
                print('simple copying ' + att_name)
            dest.attrs[att_name] = clean_string_att(att_val)

    if not skip_refs:
        # This can be copied across files without problems
        mesg = 'Could not copy region references to {}.'.format(dest.name)
        if isinstance(dest, h5py.Dataset):
            try:
                if verbose:
                    print('requested reg ref copy')
                #copy_region_refs(source, dest)
                pass ### TODO activate again
            
            except TypeError:
                warn(mesg)
        else:
            warn('Cannot copy region references to {}'.format(type(dest)))

    return dest


def validate_main_dset(h5_main, must_be_h5):
    """
    Checks to make sure that the provided object is a NSID main dataset
    Errors in parameters will result in Exceptions
    Parameters
    ----------
    h5_main : h5py.Dataset or numpy.ndarray or Dask.array.core.array
        object that represents the NSID main data
    must_be_h5 : bool
        Set to True if the expecting an h5py.Dataset object.
        Set to False if expecting a numpy.ndarray or Dask.array.core.array
    Returns
    -------
    """
    # Check that h5_main is a dataset
    if must_be_h5:
        if not isinstance(h5_main, h5py.Dataset):
            raise TypeError('{} is not an HDF5 Dataset object.'.format(h5_main))
    else:
        if not isinstance(h5_main, (np.ndarray, da.core.Array)):
            raise TypeError('raw_data should either be a np.ndarray or a da.core.Array')

    # Check dimensionality
    if len(h5_main.shape) != len(h5_main.dims):
        raise ValueError('Main data does not have full set of dimensional '
                         'scales. Provided object has shape: {} but only {} '
                         'dimensional scales'
                         ''.format(h5_main.shape, len(h5_main.dims)))


def validate_anc_h5_dsets(h5_inds, h5_vals, main_shape, is_spectroscopic=True):
    """
    Checks ancillary HDF5 datasets against shape of a main dataset.
    Errors in parameters will result in Exceptions
    Parameters
    ----------
    h5_inds : h5py.Dataset
        HDF5 dataset corresponding to the ancillary Indices dataset
    h5_vals : h5py.Dataset
        HDF5 dataset corresponding to the ancillary Values dataset
    main_shape : array-like
        Shape of the main dataset expressed as a tuple or similar
    is_spectroscopic : bool, Optional. Default = True
        set to True if ``dims`` correspond to Spectroscopic Dimensions.
        False otherwise.
    """
    if not isinstance(h5_inds, h5py.Dataset):
        raise TypeError('h5_inds must be a h5py.Dataset object')
    if not isinstance(h5_vals, h5py.Dataset):
        raise TypeError('h5_vals must be a h5py.Dataset object')
    if h5_inds.shape != h5_vals.shape:
        raise ValueError('h5_inds: {} and h5_vals: {} should be of the same '
                         'shape'.format(h5_inds.shape, h5_vals.shape))
    if isinstance(main_shape, (list, tuple)):
        if not contains_integers(main_shape, min_val=1) or \
                len(main_shape) != 2:
            raise ValueError("'main_shape' must be a valid HDF5 dataset shape")
    else:
        raise TypeError('main_shape should be of the following types:'
                        'h5py.Dataset, tuple, or list. {} provided'
                        ''.format(type(main_shape)))

    if h5_inds.shape[is_spectroscopic] != main_shape[is_spectroscopic]:
        raise ValueError('index {} in shape of h5_inds: {} and main_data: {} '
                         'should be equal'.format(int(is_spectroscopic),
                                                  h5_inds.shape, main_shape))


def validate_dims_against_main(main_shape, dims, is_spectroscopic=True):
    """
    Checks Dimension objects against a given shape for main datasets.
    Errors in parameters will result in Exceptions
    Parameters
    ----------
    main_shape : array-like
        Tuple or list with the shape of the main data
    dims : iterable
        List of Dimension objects
    is_spectroscopic : bool, Optional. Default = True
        set to True if ``dims`` correspond to Spectroscopic Dimensions.
        False otherwise.
    """
    if not isinstance(main_shape, (list, tuple)):
        raise TypeError('main_shape should be a list or tuple. Provided object'
                        ' was of type: {}'.format(type(main_shape)))
    if len(main_shape) != 2:
        raise ValueError('"main_shape" should be of length 2')
    contains_integers(main_shape, min_val=1)

    if isinstance(dims, Dimension):
        dims = [dims]
    elif not isinstance(dims, (list, tuple)):
        raise TypeError('"dims" must be a list or tuple of nsid.Dimension '
                        'objects. Provided object was of type: {}'
                        ''.format(type(dims)))
    if not all([isinstance(obj, Dimension) for obj in dims]):
        raise TypeError('One or more objects in "dims" was not nsid.Dimension')

    if is_spectroscopic:
        main_dim = 1
        dim_category = 'Spectroscopic'
    else:
        main_dim = 0
        dim_category = 'Position'

    # TODO: This is where the dimension type will need to be taken into account
    lhs = main_shape[main_dim]
    rhs = np.product([len(x.values) for x in dims])
    if lhs != rhs:
        raise ValueError(dim_category +
                         ' dimensions in main data of size: {} do not match '
                         'with product of values in provided Dimension objects'
                         ': {}'.format(lhs, rhs))


def check_if_main(h5_main, verbose=False):
    """
    Checks the input dataset to see if it has all the necessary
    features to be considered a Main dataset.  This means it is
    dataset has dimensions of correct size and has the following attributes:
    * quantity
    * units
    * main_data_name
    * data_type
    * modality
    * source
    In addition, the shapes of the ancillary matrices should match with that of
    h5_main
    Parameters
    ----------
    h5_main : HDF5 Dataset
        Dataset of interest
    verbose : Boolean (Optional. Default = False)
        Whether or not to print statements
    Returns
    -------
    success : Boolean
        True if all tests pass
    """
    try:
        validate_main_dset(h5_main, True)
    except Exception as exep:
        if verbose:
            print(exep)
        return False

    h5_name = h5_main.name.split('/')[-1]
    h5_group = h5_main.parent

    success = True

    # Check for Datasets
    
    attrs_names = ['dimension_type', 'name', 'nsid_version', 'quantity', 'units', ]
    attr_success = []
    length_success = []
    dset_success = []
    ### Check for Validity of Dimensional Scales 
    for i, dimension in enumerate(h5_main.dims):
        # check for all required attributes
        h5_dim_dset =  h5_group[dimension.label]
        attr_success.append(np.all([att in h5_dim_dset.attrs for att in attrs_names]))
        dset_success.append(np.all([attr_success, isinstance(h5_dim_dset, h5py.Dataset)]))
        # dimensional scale has to be 1D
        if len(h5_dim_dset.shape) == 1:
            # and of the same length as the shape of the dataset
            length_success.append(h5_main.shape[i] == h5_dim_dset.shape[0] )
        else:
            length_success.append(False)
    # We have the list now and can get error messages according to which dataset is bad or not.
    if np.all([np.all(attr_success),np.all(length_success), np.all(dset_success)]):
        if verbose:
            print ('Dimensions: All Attributes: ', np.all(attr_success))
            print ('Dimensions: All Correct Length: ',np.all(length_success))
            print ('Dimensions: All h5 Datasets: ',np.all(dset_success))
    else:
        #print('Dimensions are wrong') #Could be more specific
        print('length of dimension scale {length_success.index(False)} is wrong')
        print('attributes in dimension scale {attr_success.index(False)} are wrong')
        print('dimension scale {dset_success.index(False)} is not a dataset')
        return False



    #Check for all required attributes in dataset
    main_attrs_names = ['quantity', 'units', 'main_data_name','data_type', 'modality', 'source']
    main_attr_success = np.all([att in h5_main.attrs for att in main_attrs_names])
    if verbose:
        print('All Attributes in dataset: ', main_attr_success)
    if not main_attr_success:
        if verbose:
            print('{} does not have the mandatory attributes'.format(h5_main.name))
        return False

    for attr_name in main_attrs_names:
        val = get_attr(h5_main, attr_name)
        if not isinstance(val, (str, unicode)):
            if verbose:
                print('Attribute {} of {} found to be {}. Expected a string'.format(attr_name, h5_main.name, val))
            return False

    
    return main_attr_success


def link_as_main(h5_main, dim_dict):
    """
    Attaches datasets as h5 Dimensional Scales to  `h5_main`
    Parameters
    ----------
    h5_main : h5py.Dataset
        N-dimensional Dataset which will have the references added as h5 Dimensional Scales
    dim_dict: dictionary with dimensional oreder as key and items are datasets to be used as h5 Dimensional Scales 

    Returns
    -------
    pyNSID.NSIDataset
        NSIDataset version of h5_main now that it is a NSID Main dataset
    """
    if not isinstance(h5_main, h5py.Dataset):
        raise TypeError('h5_main should be a h5py.Dataset object')

    h5_parent_group = h5_main.parent
    main_shape = h5_main.shape
    ######################
    # Validate Dimensions
    ######################
    # An N dimensional dataset should have N items in the dimension dictionary
    if len(dim_dict) != len(main_shape):
        raise ValueError('Incorrect number of dimensions: {} provided to support main data, of shape: {}'.format(len(dim_dict), main_shape))
    if set(range(len(main_shape))) != set(dim_dict.keys()):
        raise KeyError('')
    
    dimensions_correct = []
    dim_names = []
    for index, dim_exp_size in enumerate(main_shape):
        this_dim = dim_dict[index]
        if isinstance(this_dim, h5py.Dataset):
            error_message = validate_dimensions(this_dim, main_shape[index])
            if len(error_message)>0:
                raise TypeError('Dimension {} has the following error_message:\n'.format(index), error_message)
            else:
                #if this_dim.name not in dim_names:
                if this_dim.name not in dim_names: ## names must be unique
                    dim_names.append(this_dim.name)
                else:
                    raise TypeError('All dimension names must be unique, found {} twice'.format(this_dim.name))
                if this_dim.file != h5_parent_group.file:
                    this_dim = copy_dataset(this_dim, h5_parent_group, verbose=False)
        else: 
            raise TypeError('Items in dictionary must all  be h5py.Datasets !')

    ################
    # Attach Scales
    ################
    for i, this_dim_dset in  dim_dict.items():
        this_dim_dset.make_scale(this_dim_dset.attrs['name'])
        h5_main.dims[int(i)].label = this_dim_dset.attrs['name']
        h5_main.dims[int(i)].attach_scale(this_dim_dset)
        
    from ..nsi_data import NSIDataset
    try:
        # If all other conditions are satisfied
        return NSIDataset(h5_main)
    except TypeError:
        # If some other conditions are yet to be satisfied
        return h5_main


def check_for_old(h5_base, tool_name, new_parms=None, target_dset=None,
                  h5_parent_goup=None, verbose=False):
    """
    Check to see if the results of a tool already exist and if they
    were performed with the same parameters.
    Parameters
    ----------
    h5_base : h5py.Dataset object
           Dataset on which the tool is being applied to
    tool_name : str
           process or analysis name
    new_parms : dict, optional
           Parameters with which this tool will be performed.
    target_dset : str, optional, default = None
            Name of the dataset whose attributes will be compared against new_parms.
            Default - checking against the group
    h5_parent_goup : h5py.Group, optional. Default = None
            The group to search under. Use this option when `h5_base` and
            the potential results groups (within `h5_parent_goup` are located
            in different HDF5 files. Default - search within h5_base.parent
    verbose : bool, optional, default = False
           Whether or not to print debugging statements
    Returns
    -------
    group : list
           List of all :class:`h5py.Group` objects with parameters matching those in `new_parms`
    """
    if not isinstance(h5_base, h5py.Dataset):
        raise TypeError('h5_base should be a h5py.Dataset object')
    tool_name = validate_single_string_arg(tool_name, 'tool_name')

    if h5_parent_goup is not None:
        if not isinstance(h5_parent_goup, (h5py.File, h5py.Group)):
            raise TypeError("'h5_parent_group' should either be a h5py.File "
                            "or h5py.Group object")
    else:
        h5_parent_goup = h5_base.parent

    if new_parms is None:
        new_parms = dict()
    else:
        if not isinstance(new_parms, dict):
            raise TypeError('new_parms should be a dict')
    if target_dset is not None:
        target_dset = validate_single_string_arg(target_dset, 'target_dset')

    matching_groups = []
    groups = find_results_groups(h5_base, tool_name,
                                 h5_parent_group=h5_parent_goup)

    for group in groups:
        if verbose:
            print('Looking at group - {}'.format(group.name.split('/')[-1]))

        h5_obj = group
        if target_dset is not None:
            if target_dset in group.keys():
                h5_obj = group[target_dset]
            else:
                if verbose:
                    print('{} did not contain the target dataset: {}'.format(group.name.split('/')[-1],
                                                                             target_dset))
                continue

        if check_for_matching_attrs(h5_obj, new_parms=new_parms, verbose=verbose):
            # return group
            matching_groups.append(group)

    return matching_groups


def get_source_dataset(h5_group):
    """
    Find the name of the source dataset used to create the input `h5_group`,
    so long as the source dataset is in the same HDF5 file
    Parameters
    ----------
    h5_group : :class:`h5py.Group`
        Child group whose source dataset will be returned
    Returns
    -------
    h5_source : NSIDataset object
        Main dataset from which this group was generated
    """
    if not isinstance(h5_group, h5py.Group):
        raise TypeError('h5_group should be a h5py.Group object')

    h5_parent_group = h5_group.parent
    group_name = h5_group.name.split('/')[-1]
    # What if the group name was not formatted according to Pycroscopy rules?
    name_split = group_name.split('-')
    if len(name_split) != 2:
        raise ValueError("The provided group's name could not be split by '-' as expected in "
                         "SourceDataset-ProcessName_000")
    h5_source = h5_parent_group[name_split[0]]

    if not isinstance(h5_source, h5py.Dataset):
        raise ValueError('Source object was not a dataset!')

    from ..nsi_data import NSIDataset

    return NSIDataset(h5_source)


def assign_group_index(h5_parent_group, base_name, verbose=False):
    """
    Searches the parent h5 group to find the next available index for the group
    Parameters
    ----------
    h5_parent_group : :class:`h5py.Group` object
        Parent group under which the new group object will be created
    base_name : str or unicode
        Base name of the new group without index
    verbose : bool, optional. Default=False
        Whether or not to print debugging statements
    Returns
    -------
    base_name : str or unicode
        Base name of the new group with the next available index as a suffix
    """
    if not isinstance(h5_parent_group, h5py.Group):
        raise TypeError('h5_parent_group should be a h5py.Group object')
    base_name = validate_single_string_arg(base_name, 'base_name')

    if len(base_name) == 0:
        raise ValueError('base_name should not be an empty string')

    if not base_name.endswith('_'):
        base_name += '_'

    temp = [key for key in h5_parent_group.keys()]
    if verbose:
        print('Looking for group names starting with {} in parent containing items: '
              '{}'.format(base_name, temp))
    previous_indices = []
    for item_name in temp:
        if isinstance(h5_parent_group[item_name], h5py.Group) and item_name.startswith(base_name):
            previous_indices.append(int(item_name.replace(base_name, '')))
    previous_indices = np.sort(previous_indices)
    if verbose:
        print('indices of existing groups with the same prefix: {}'.format(previous_indices))
    if len(previous_indices) == 0:
        index = 0
    else:
        index = previous_indices[-1] + 1
    return base_name + '{:03d}'.format(index)


def create_indexed_group(h5_parent_group, base_name):
    """
    Creates a group with an indexed name (eg - 'Measurement_012') under h5_parent_group using the provided base_name
    as a prefix for the group's name
    Parameters
    ----------
    h5_parent_group : :class:`h5py.Group` or :class:`h5py.File`
        File or group within which the new group will be created
    base_name : str or unicode
        Prefix for the group name. This need not end with a '_'. It will be added automatically
    Returns
    -------
    """
    if not isinstance(h5_parent_group, (h5py.Group, h5py.File)):
        raise TypeError('h5_parent_group should be a h5py.File or Group object')
    base_name = validate_single_string_arg(base_name, 'base_name')

    group_name = assign_group_index(h5_parent_group, base_name)
    h5_new_group = h5_parent_group.create_group(group_name)
    write_book_keeping_attrs(h5_new_group)
    return h5_new_group


def create_results_group(h5_main, tool_name, h5_parent_group=None):
    """
    Creates a h5py.Group object autoindexed and named as 'DatasetName-ToolName_00x'
    Parameters
    ----------
    h5_main : h5py.Dataset object
        Reference to the dataset based on which the process / analysis is being performed
    tool_name : string / unicode
        Name of the Process / Analysis applied to h5_main
    h5_parent_group : h5py.Group, optional. Default = None
        Parent group under which the results group will be created. Use this
        option to write results into a new HDF5 file. By default, results will
        be written into the same group containing `h5_main`
    Returns
    -------
    h5_group : :class:`h5py.Group`
        Results group which can now house the results datasets
    """
    if not isinstance(h5_main, h5py.Dataset):
        raise TypeError('h5_main should be a h5py.Dataset object')
    if h5_parent_group is not None:
        if not isinstance(h5_parent_group, (h5py.File, h5py.Group)):
            raise TypeError("'h5_parent_group' should either be a h5py.File "
                            "or h5py.Group object")
    else:
        h5_parent_group = h5_main.parent

    tool_name = validate_single_string_arg(tool_name, 'tool_name')

    if '-' in tool_name:
        warn('tool_name should not contain the "-" character. Reformatted name from:{} to '
             '{}'.format(tool_name, tool_name.replace('-', '_')))
    tool_name = tool_name.replace('-', '_')

    group_name = h5_main.name.split('/')[-1] + '-' + tool_name + '_'
    group_name = assign_group_index(h5_parent_group, group_name)

    h5_group = h5_parent_group.create_group(group_name)

    write_book_keeping_attrs(h5_group)

    # Also add some basic attributes like source and tool name. This will allow relaxation of nomenclature restrictions:
    # this are NOT being used right now but will be in the subsequent versions of pyNSID
    write_simple_attrs(h5_group, {'tool': tool_name, 'num_source_dsets': 1})
    # in this case, there is only one source
    if h5_parent_group.file == h5_main.file:
        for dset_ind, dset in enumerate([h5_main]):
            h5_group.attrs['source_' + '{:03d}'.format(dset_ind)] = dset.ref

    return h5_group


def copy_main_attributes(h5_main, h5_new):
    """
    Copies the units and quantity name from one dataset to another
    Parameters
    ----------
    h5_main : h5py.Dataset
        Dataset containing the target attributes
    h5_new : h5py.Dataset
        Dataset to which the target attributes are to be copied
    """
    for param, param_name in zip([h5_main, h5_new], ['h5_main', 'h5_new']):
        if not isinstance(param, h5py.Dataset):
            raise TypeError(param_name + ' should be a h5py.Dataset object')

    for att_name in ['quantity', 'units']:
        if att_name not in h5_main.attrs:
            raise KeyError('Attribute: {} does not exist in {}'.format(att_name, h5_main))
        val = get_attr(h5_main, att_name)
        h5_new.attrs[att_name] = clean_string_att(val)


def create_empty_dataset(source_dset, dtype, dset_name, h5_group=None, new_attrs=None, skip_refs=False):
    """
    Creates an empty dataset in the h5 file based on the provided dataset in the same or specified group
    Parameters
    ----------
    source_dset : h5py.Dataset object
        Source object that provides information on the group and shape of the dataset
    dtype : dtype
        Data type of the fit / guess datasets
    dset_name : String / Unicode
        Name of the dataset
    h5_group : :class:`h5py.Group`, optional. Default = None
        Group within which this dataset will be created
    new_attrs : dictionary (Optional)
        Any new attributes that need to be written to the dataset
    skip_refs : boolean, optional
        Should ObjectReferences and RegionReferences be skipped when copying attributes from the
        `source_dset`
    Returns
    -------
    h5_new_dset : h5py.Dataset object
        Newly created dataset
    """
    if not isinstance(source_dset, h5py.Dataset):
        raise TypeError('source_deset should be a h5py.Dataset object')
    _ = validate_dtype(dtype)
    if new_attrs is not None:
        if not isinstance(new_attrs, dict):
            raise TypeError('new_attrs should be a dictionary')
    else:
        new_attrs = dict()

    if h5_group is None:
        h5_group = source_dset.parent
    else:
        if not isinstance(h5_group, (h5py.Group, h5py.File)):
            raise TypeError('h5_group should be a h5py.Group or h5py.File object')

        if source_dset.file != h5_group.file and not skip_refs:
            # Cannot carry over references
            warn('H5 object references will not be copied over since {} is in '
                 'a different HDF5 file as {}'.format(h5_group, source_dset))
            skip_refs = True

    dset_name = validate_single_string_arg(dset_name, 'dset_name')
    if '-' in dset_name:
        warn('dset_name should not contain the "-" character. Reformatted name from:{} to '
             '{}'.format(dset_name, dset_name.replace('-', '_')))
    dset_name = dset_name.replace('-', '_')

    kwargs = {'shape': source_dset.shape, 'dtype': dtype, 'compression': source_dset.compression,
              'chunks': source_dset.chunks}

    if source_dset.file.driver == 'mpio':
        if kwargs.pop('compression', None) is not None:
            warn('This HDF5 file has been opened with the "mpio" communicator. '
                 'mpi4py does not allow creation of compressed datasets. Compression kwarg has been removed')

    if dset_name in h5_group.keys():
        if isinstance(h5_group[dset_name], h5py.Dataset):
            warn('A dataset named: {} already exists in group: {}'.format(dset_name, h5_group.name))
            h5_new_dset = h5_group[dset_name]
            # Make sure it has the correct shape and dtype
            if any((source_dset.shape != h5_new_dset.shape, dtype != h5_new_dset.dtype)):
                warn('Either the shape (existing: {} desired: {}) or dtype (existing: {} desired: {}) of the dataset '
                     'did not match with expectations. Deleting and creating a new one.'.format(h5_new_dset.shape,
                                                                                                source_dset.shape,
                                                                                                h5_new_dset.dtype,
                                                                                                dtype))
                del h5_new_dset, h5_group[dset_name]
                h5_new_dset = h5_group.create_dataset(dset_name, **kwargs)
        else:
            raise KeyError('{} is already a {} in group: {}'.format(dset_name, type(h5_group[dset_name]),
                                                                    h5_group.name))

    else:
        h5_new_dset = h5_group.create_dataset(dset_name, **kwargs)

    


    # This should link the ancillary datasets correctly
    h5_new_dset = copy_attributes(source_dset, h5_new_dset,
                                  skip_refs=skip_refs)

    ####################
    # Attaching Dimensional Scales
    ####################
    dim_dict = {}
    for i, dimension in enumerate(source_dset.dims):
        # check for all required attributes
        dim_dict[i]=h5_group[dimension.label]

    h5_new_dset = link_as_main(h5_new_dset,dim_dict)

    ###################
    #  Go on with old function
    ################                              
    if source_dset.file != h5_group.file:
        copy_linked_objects(source_dset, h5_new_dset)
    h5_new_dset.attrs.update(new_attrs)

    if check_if_main(h5_new_dset):
        from ..nsi_data import NSIDataset

        h5_new_dset = NSIDataset(h5_new_dset)
        # update book keeping attributes
        write_book_keeping_attrs(h5_new_dset)

    return h5_new_dset


def check_for_matching_attrs(h5_obj, new_parms=None, verbose=False):
    """
    Compares attributes in the given H5 object against those in the provided dictionary and returns True if
    the parameters match, and False otherwise
    Parameters
    ----------
    h5_obj : h5py object (Dataset or :class:`h5py.Group`)
        Object whose attributes will be compared against new_parms
    new_parms : dict, optional. default = empty dictionary
        Parameters to compare against the attributes present in h5_obj
    verbose : bool, optional, default = False
       Whether or not to print debugging statements
    Returns
    -------
    tests: bool
        Whether or not all paramters in new_parms matched with those in h5_obj's attributes
    """
    if not isinstance(h5_obj, (h5py.Dataset, h5py.Group, h5py.File)):
        raise TypeError('h5_obj should be a h5py.Dataset, h5py.Group, or h5py.File object')
    if new_parms is None:
        new_parms = dict()
    else:
        if not isinstance(new_parms, dict):
            raise TypeError('new_parms should be a dictionary')

    tests = []
    for key in new_parms.keys():

        if verbose:
            print('Looking for new attribute named: {}'.format(key))

        # HDF5 cannot store None as an attribute anyway. ignore
        if new_parms[key] is None:
            continue

        try:
            old_value = get_attr(h5_obj, key)
        except KeyError:
            # if parameter was not found assume that something has changed
            if verbose:
                print('New parm: {} \t- new parm not in group *****'.format(key))
            tests.append(False)
            break

        if isinstance(old_value, np.ndarray):
            if not isinstance(new_parms[key], collections.Iterable):
                if verbose:
                    print('New parm: {} \t- new parm not iterable unlike old parm *****'.format(key))
                tests.append(False)
                break
            new_array = np.array(new_parms[key])
            if old_value.size != new_array.size:
                if verbose:
                    print('New parm: {} \t- are of different sizes ****'.format(key))
                tests.append(False)
            else:
                try:
                    answer = np.allclose(old_value, new_array)
                except TypeError:
                    # comes here when comparing string arrays
                    # Not sure of a better way
                    answer = []
                    for old_val, new_val in zip(old_value, new_array):
                        answer.append(old_val == new_val)
                    answer = np.all(answer)
                if verbose:
                    print('New parm: {} \t- match: {}'.format(key, answer))
                tests.append(answer)
        else:
            """if isinstance(new_parms[key], collections.Iterable):
                if verbose:
                    print('New parm: {} \t- new parm is iterable unlike old parm *****'.format(key))
                tests.append(False)
                break"""
            answer = np.all(new_parms[key] == old_value)
            if verbose:
                print('New parm: {} \t- match: {}'.format(key, answer))
            tests.append(answer)
    if verbose:
        print('')

    return all(tests)



def copy_dataset(h5_orig_dset, h5_dest_grp, alias=None, verbose=False):
    """
    Copies the provided HDF5 dataset to the provided destination. This function
    is handy when needing to make copies of datasets to a different HDF5 file.
    Notes
    -----
    This function does NOT copy all linked objects such as ancillary
    datasets. Call `copy_linked_objects` to accomplish that goal.
    Parameters
    ----------
    h5_orig_dset : h5py.Dataset
    h5_dest_grp : h5py.Group or h5py.File object :
        Destination where the duplicate dataset will be created
    alias : str, optional. Default = name from `h5_orig_dset`:
        Name to be assigned to the copied dataset
    verbose : bool, optional. Default = False
        Whether or not to print logs to assist in debugging
    Returns
    -------
    """
    if not isinstance(h5_orig_dset, h5py.Dataset):
        raise TypeError("'h5_orig_dset' should be a h5py.Dataset object")
    if not isinstance(h5_dest_grp, (h5py.File, h5py.Group)):
        raise TypeError("'h5_dest_grp' should either be a h5py.File or "
                        "h5py.Group object")
    if alias is not None:
        validate_single_string_arg(alias, 'alias')
    else:
        alias = h5_orig_dset.name.split('/')[-1]

    if alias in h5_dest_grp.keys():
        if verbose:
            warn('{} already contains an object with the same name: {}'
                 ''.format(h5_dest_grp, alias))
        h5_new_dset = h5_dest_grp[alias]
        if not isinstance(h5_new_dset, h5py.Dataset):
            raise TypeError('{} already contains an object: {} with the desired'
                           ' name which is not a dataset'.format(h5_dest_grp,
                                                                 h5_new_dset))

        da_source = lazy_load_array(h5_orig_dset)
        da_dest = lazy_load_array(h5_new_dset)

        if da_source.shape != da_dest.shape:
            raise ValueError('Existing dataset: {} has a different shape '
                             'compared to the original dataset: {}'
                             ''.format(h5_new_dset, h5_orig_dset))
        if not da.allclose(da_source, da_dest):
            raise ValueError('Existing dataset: {} has different contents'
                             'compared to the original dataset: {}'
                             ''.format(h5_new_dset, h5_orig_dset))
    else:

        kwargs = {'shape': h5_orig_dset.shape,
                  'dtype': h5_orig_dset.dtype,
                  'compression': h5_orig_dset.compression,
                  'chunks': h5_orig_dset.chunks}
        if h5_orig_dset.file.driver == 'mpio':
            if kwargs.pop('compression', None) is not None:
                warn('This HDF5 file has been opened wth the '
                     '"mpio" communicator. mpi4py does not allow '
                     'creation of compressed datasets. Compression'
                     ' kwarg has been removed')
        if verbose:
            print('Creating new HDF5 dataset named: {} at: {} with'
                  ' kwargs: {}'.format(alias, h5_dest_grp,
                                       kwargs))
        h5_new_dset = h5_dest_grp.create_dataset(alias,
                                                **kwargs)
        if verbose:
            print('dask.array will copy data from source dataset '
                  'to new dataset')
        da.to_hdf5(h5_new_dset.file.filename,
                   {h5_new_dset.name: lazy_load_array(h5_orig_dset)})
    if verbose:
        print('Copying simple attributes of original dataset: {} to '
              'destination dataset: {}'.format(h5_orig_dset, h5_new_dset))

    copy_attributes(h5_orig_dset, h5_new_dset, skip_refs=True)
    #copy_all_region_refs(h5_orig_dset, h5_new_dset)

    return h5_new_dset


def copy_linked_objects(h5_source, h5_dest, verbose=False):
    """
    Recursively copies datasets linked to the source h5 object to the
    destination h5 object that are be in different HDF5 files.
    
    This is for copying ancillary datasets to a target dataset that is 
    missing ancillary datasets. It is not meant for copying to a Group,
    but that is supported.
    Notes
    -----
    We anticipate this function being used to copy over ancillary datasets
    Parameters
    ----------
    h5_source : h5py.Dataset or h5py.Group object
        Source object
    h5_dest : h5py.Dataset or h5py.Group object
        Destination object
    verbose : bool, optional. Default: False
        Whether or not to print logs for debugging purposes
    """
    try:
        # The following line takes care of object validation
        validate_h5_objs_in_same_h5_file(h5_source, h5_dest)
        same_file = True
    except ValueError:
        same_file = False

    if same_file:
        warn('{} and {} are in the same HDF5 file. Consider copying references'
             ' instead of copying linked objects'.format(h5_source, h5_dest))
        return

    if isinstance(h5_dest, h5py.Group):
        h5_dest_grp = h5_dest
    else:
        h5_dest_grp = h5_dest.parent

    # Now we are working on other files
    for link_obj_name in h5_source.attrs.keys():
        h5_orig_obj = get_attr(h5_source, link_obj_name)
        if isinstance(h5_orig_obj, h5py.Reference) and not \
                isinstance(h5_orig_obj, h5py.RegionReference):
            h5_orig_obj = h5_source.file[h5_orig_obj]
            if verbose:
                print('Attempting to copy object linked to source: {} as {}'
                      ''.format(h5_orig_obj, link_obj_name))
            # Check to see if such a dataset already exist
            if link_obj_name in h5_dest_grp.keys():
                h5_new_obj = h5_dest_grp[link_obj_name]
                warn('An object with the same name: {} already exists in the '
                     'destination group: {}'.format(h5_new_obj, h5_dest_grp.name))
                if type(h5_dest_grp[link_obj_name]) != type(h5_orig_obj):
                    mesg = 'Destination parent: {} already has a child named' \
                           ' {} that is of type: {} which does not match ' \
                           'with that of the object linked with the source ' \
                           'dataset: {}'.format(h5_dest_grp, link_obj_name,
                                                type(h5_orig_obj),
                                                type(h5_new_obj))
                    raise TypeError(mesg)

                elif isinstance(h5_new_obj, h5py.Dataset):
                    _ = copy_dataset(h5_orig_obj, h5_dest_grp,
                                     alias=link_obj_name, verbose=verbose)
                    h5_dest.attrs[link_obj_name] = h5_new_obj.ref
                    continue
                elif isinstance(h5_new_obj, h5py.Group):
                    raise ValueError('Destination already contains another '
                                     'HDF5 group: {} with the same name as '
                                     'the source: {}'.format(h5_new_obj,
                                                             h5_orig_obj))
                else:
                    raise NotImplementedError('Unable to copy {} objects yet'
                                              '. Contact developer if you need'
                                              ' this'
                                              ''.format(type(h5_orig_obj)))
            else:
                if isinstance(h5_orig_obj, h5py.Dataset):
                    h5_new_obj = copy_dataset(h5_orig_obj, h5_dest_grp,
                                              alias=link_obj_name,
                                              verbose=verbose)
                    h5_dest.attrs[link_obj_name] = h5_new_obj.ref
                else:
                    raise NotImplementedError('Unable to copy {} objects yet'
                                              '. Contact developer if you need'
                                              ' this'.format(type(h5_orig_obj)))


def copy_region_refs(h5_source, h5_target):
    """
    Check the input dataset for plot groups, copy them if they exist
    Also make references in the Spectroscopic Values and Indices tables
    Parameters
    ----------
    h5_source : HDF5 Dataset
            source dataset to copy references from
    h5_target : HDF5 Dataset
            target dataset the references from h5_source are copied to
    """
    '''
    Check both h5_source and h5_target to ensure that are Main
    '''
    # TODO: Move this vestige to pycroscopy. Use copy_all_region_refs instead
    are_main = all([check_if_main(h5_source), check_if_main(h5_target)])
    if not all([isinstance(h5_source, h5py.Dataset), isinstance(h5_target, h5py.Dataset)]):
        raise TypeError('Inputs to copy_region_refs must be HDF5 Datasets')

    # It is OK if objects are in different files

    
    for key in h5_source.attrs.keys():
        if not isinstance(h5_source.attrs[key], h5py.RegionReference):
            continue

        if are_main:# TODO: Region Reference copy not implemented
            #for dim in h5_source_inds.dims:
            #ref_inds = simple_region_ref_copy(h5_source, h5_target, key)

            #else:
            '''
            Spectroscopic dimensions are different.
            Do the dimension reducing copy.
            '''
            #ref_inds = copy_reg_ref_reduced_dim(h5_source, h5_target, h5_source_inds, h5_spec_inds, key)

            '''
            Create references for Spectroscopic Indices and Values
            Set the end-point of each hyperslab in the position dimension to the number of
            rows in the index array
            '''

            ref_inds[:, 1, 0][ref_inds[:, 1, 0] > h5_spec_inds.shape[0]] = h5_spec_inds.shape[0] - 1
            spec_inds_ref = create_region_reference(h5_spec_inds, ref_inds)
            h5_spec_inds.attrs[key] = spec_inds_ref
            spec_vals_ref = create_region_reference(h5_spec_vals, ref_inds)
            h5_spec_vals.attrs[key] = spec_vals_ref

        else:
            '''
            If not main datasets, then only simple copy can be used.
            '''
            simple_region_ref_copy(h5_source, h5_target, key)



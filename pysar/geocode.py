#! /usr/bin/env python2
############################################################
# Program is part of PySAR v1.2                            #
# Copyright(c) 2017, Zhang Yunjun                          #
# Author:  Zhang Yunjun                                    #
############################################################

import os
import sys
import time
import argparse
import warnings

import h5py
import numpy as np
from scipy.interpolate import RegularGridInterpolator as RGI

import pysar._datetime as ptime
import pysar._readfile as readfile
import pysar._writefile as writefile
import pysar._pysar_utilities as ut
from pysar._readfile import multi_group_hdf5_file, multi_dataset_hdf5_file, single_dataset_hdf5_file


###############################################################################
def update_attribute4isce(atr_rdr, inps, geo_data):
    '''Get attributes in geo coord from atr_rdr dict and geo_data matrix
    Inputs:
        atr_rdr - dict, attribute of file in radar coord
        inps    - Namespace, including items of the following:
                  lat0/lon0
                  lat_step/lon_step
                  lat/lon - 1D np.array of lat/lon value
        geo_data - 2D matrix, with shape info used.
    Output:
        atr - dict, attributes of output file in geo coord.
    '''

    # copy atr_rdr
    atr = dict()
    for key, value in atr_rdr.iteritems():
        atr[key] = str(value)

    atr['FILE_LENGTH'] = str(geo_data.shape[0])
    atr['WIDTH'] = str(geo_data.shape[1])
    atr['Y_FIRST'] = str(inps.lat0)
    atr['X_FIRST'] = str(inps.lon0)
    atr['Y_STEP'] = str(inps.lat_step)
    atr['X_STEP'] = str(inps.lon_step)
    atr['Y_UNIT'] = 'degrees'
    atr['X_UNIT'] = 'degrees'

    if 'ref_y' in atr_rdr.keys() and 'ref_x' in atr_rdr.keys():
        length_rdr = int(atr_rdr['FILE_LENGTH'])
        width_rdr = int(atr_rdr['WIDTH'])
        ref_y_rdr = int(atr_rdr['ref_y'])
        ref_x_rdr = int(atr_rdr['ref_x'])

        ref_lat = inps.lat.reshape(length_rdr, width_rdr)[ref_y_rdr,ref_x_rdr]
        ref_lon = inps.lon.reshape(length_rdr, width_rdr)[ref_y_rdr,ref_x_rdr]
        ref_y = int(np.rint((ref_lat - inps.lat0)/inps.lat_step))
        ref_x = int(np.rint((ref_lon - inps.lon0)/inps.lon_step))

        atr['ref_lat'] = str(ref_lat)
        atr['ref_lon'] = str(ref_lon)
        atr['ref_y'] = str(ref_y)
        atr['ref_x'] = str(ref_x)

    return atr


def geocode_attribute_with_geo_lut(atr_rdr, atr_lut, print_msg=True):
    '''Get attributes in geo coord from atr_rdr dict and atr_lut dict
    Inputs:
        atr_rdr : dict, attributes of file in radar coord
        atr_lut : dict, attributes of mapping transformation file
        print_msg : bool, print out message or not
    Output:
        atr : dict, attributes of output file in geo coord.
    '''

    # copy atr_rdr
    atr = dict()
    for key, value in atr_rdr.iteritems():
        atr[key] = str(value)

    atr['FILE_LENGTH'] = atr_lut['FILE_LENGTH']
    atr['WIDTH']   = atr_lut['WIDTH']
    atr['Y_FIRST'] = atr_lut['Y_FIRST']
    atr['X_FIRST'] = atr_lut['X_FIRST']
    atr['Y_STEP']  = atr_lut['Y_STEP']
    atr['X_STEP']  = atr_lut['X_STEP']
    try:    atr['Y_UNIT'] = atr_lut['Y_UNIT']
    except: atr['Y_UNIT'] = 'degrees'
    try:    atr['X_UNIT'] = atr_lut['X_UNIT']
    except: atr['X_UNIT'] = 'degrees'

    # Reference point from y/x to lat/lon
    if 'ref_y' in atr_rdr.keys() and 'ref_x' in atr_rdr.keys():
        ref_x_rdr = np.array(int(atr_rdr['ref_x']))
        ref_y_rdr = np.array(int(atr_rdr['ref_y']))
        trans_file = atr_lut['FILE_PATH']
        ref_lat, ref_lon = ut.radar2glob(ref_y_rdr, ref_x_rdr, trans_file, atr_rdr, print_msg=False)[0:2]
        if ~np.isnan(ref_lat) and ~np.isnan(ref_lon):
            ref_y = np.rint((ref_lat - float(atr['Y_FIRST'])) / float(atr['Y_STEP']))
            ref_x = np.rint((ref_lon - float(atr['X_FIRST'])) / float(atr['X_STEP']))
            atr['ref_lat'] = str(ref_lat)
            atr['ref_lon'] = str(ref_lon)
            atr['ref_y'] = str(int(ref_y))
            atr['ref_x'] = str(int(ref_x))
            if print_msg:
                print 'update ref_lat/lon/y/x'
        else:
            warnings.warn("original reference pixel is out of .trans file's coverage. Continue.")
            try: atr.pop('ref_y')
            except: pass
            try: atr.pop('ref_x')
            except: pass
            try: atr.pop('ref_lat')
            except: pass
            try: atr.pop('ref_lon')
            except: pass
    return atr


def geocode_file_with_geo_lut(fname, lut_file=None, method='nearest', fill_value=np.nan, fname_out=None):
    '''Geocode file using ROI_PAC/Gamma lookup table file.
    Related module: scipy.interpolate.RegularGridInterpolator

    Inputs:
        fname      : string, file to be geocoded
        lut_file   : string, optional, lookup table file genereated by ROIPAC or Gamma
                     i.e. geomap_4rlks.trans           from ROI_PAC
                          sim_150911-150922.UTM_TO_RDC from Gamma
        method     : string, optional, interpolation/resampling method, supporting nearest, linear
        fill_value : value used for points outside of the interpolation domain.
                     If None, values outside the domain are extrapolated.
        fname_out  : string, optional, output geocoded filename
    Output:
        fname_out  : string, optional, output geocoded filename
    '''

    start = time.time()
    ## Default Inputs and outputs
    if not fname_out:
        fname_out = 'geo_'+fname

    # Default lookup table file:
    atr_rdr = readfile.read_attribute(fname)
    if not lut_file:
        if atr_rdr['INSAR_PROCESSOR'] == 'roipac':
            lut_file = ['geomap*lks_tight.trans','geomap*lks.trans']
        elif atr_rdr['INSAR_PROCESSOR'] == 'gamma':
            lut_file = ['sim*_tight.UTM_TO_RDC','sim*.UTM_TO_RDC']

    try:    lut_file = ut.get_file_list(lut_file)[0]
    except: lut_file = None
    if not lut_file:
        sys.exit('ERROR: No lookup table file found! Can not geocoded without it.')


    ## Original coordinates: row/column number in radar file
    print '------------------------------------------------------'
    print 'geocoding file: '+fname
    len_rdr = int(atr_rdr['FILE_LENGTH'])
    wid_rdr = int(atr_rdr['WIDTH'])
    pts_rdr = (np.arange(len_rdr), np.arange(wid_rdr))


    ## New coordinates: data value in lookup table
    print 'reading lookup table file: '+lut_file
    rg, az, atr_lut = readfile.read(lut_file)
    len_geo = int(atr_lut['FILE_LENGTH'])
    wid_geo = int(atr_lut['WIDTH'])

    # adjustment if input radar file has been subseted.
    if 'subset_x0' in atr_rdr.keys():
        x0 = float(atr_rdr['subset_x0'])
        y0 = float(atr_rdr['subset_y0'])
        rg -= x0
        az -= y0
        print '\tinput radar coord file has been subsetted, adjust lookup table value'

    # extract pixels only available in radar file (get ride of invalid corners)
    idx = (az>0.0)*(az<=len_rdr)*(rg>0.0)*(rg<=wid_rdr)
    pts_geo = np.hstack((az[idx].reshape(-1,1), rg[idx].reshape(-1,1)))
    del az, rg


    print 'geocoding using scipy.interpolate.RegularGridInterpolator ...'
    data_geo = np.empty((len_geo, wid_geo)) * fill_value
    k = atr_rdr['FILE_TYPE']
    ##### Multiple Dataset File
    if k in multi_group_hdf5_file+multi_dataset_hdf5_file:
        h5 = h5py.File(fname,'r')
        epoch_list = sorted(h5[k].keys())
        epoch_num = len(epoch_list)
        prog_bar = ptime.progress_bar(maxValue=epoch_num)

        h5out = h5py.File(fname_out,'w')
        group = h5out.create_group(k)
        print 'writing >>> '+fname_out

        if k == 'timeseries':
            print 'number of acquisitions: '+str(epoch_num)
            for i in range(epoch_num):
                date = epoch_list[i]
                data = h5[k].get(date)[:]
                RGI_func = RGI(pts_rdr, data, method, bounds_error=False, fill_value=fill_value)

                data_geo.fill(fill_value)
                data_geo[idx] = RGI_func(pts_geo)

                dset = group.create_dataset(date, data=data_geo, compression='gzip')
                prog_bar.update(i+1, suffix=date)
            prog_bar.close()

            print 'update attributes'
            atr = geocode_attribute_with_geo_lut(atr_rdr, atr_lut)
            for key,value in atr.iteritems():
                group.attrs[key] = value

        elif k in ['interferograms','wrapped','coherence']:
            print 'number of interferograms: '+str(epoch_num)
            date12_list = ptime.list_ifgram2date12(epoch_list)
            for i in range(epoch_num):
                ifgram = epoch_list[i]
                data = h5[k][ifgram].get(ifgram)[:]
                RGI_func = RGI(pts_rdr, data, method, bounds_error=False, fill_value=fill_value)

                data_geo.fill(fill_value)
                data_geo[idx] = RGI_func(pts_geo)

                gg = group.create_group(ifgram)
                dset = gg.create_dataset(ifgram, data=data_geo, compression='gzip')

                atr = geocode_attribute_with_geo_lut(h5[k][ifgram].attrs, atr_lut, print_msg=False)
                for key, value in atr.iteritems():
                    gg.attrs[key] = value
                prog_bar.update(i+1, suffix=date12_list[i])
        h5.close()
        h5out.close()

    ##### Single Dataset File
    else:
        print 'reading '+fname
        data = readfile.read(fname)[0]
        RGI_func = RGI(pts_rdr, data, method, bounds_error=False, fill_value=fill_value)

        data_geo.fill(fill_value)
        data_geo[idx] = RGI_func(pts_geo)

        print 'update attributes'
        atr = geocode_attribute_with_geo_lut(atr_rdr, atr_lut)

        print 'writing >>> '+fname_out
        writefile.write(data_geo, atr, fname_out)

    del data_geo
    s = time.time()-start;  m, s = divmod(s, 60);  h, m = divmod(m, 60)
    print 'Time used: %02d hours %02d mins %02d secs' % (h, m, s)
    return fname_out


######################################################################################
EXAMPLE='''example:
  geocode.py  velocity.h5
  geocode.py  timeseries_ECMWF_demErr_refDate.h5  -l geomap_4rlks.trans
  geocode.py  101120-110220.unw   -i linear       -l geomap_4rlks.trans
  geocode.py  velocity.h5 temporalCoherence.h5 incidenceAngle.h5

  geocode.py  velocity.h5  -l sim_150911-150922.UTM_TO_RDC
'''

def cmdLineParse():
    parser = argparse.ArgumentParser(description='Geocode using lookup table',\
                                     formatter_class=argparse.RawTextHelpFormatter,\
                                     epilog=EXAMPLE)

    parser.add_argument('file', nargs='+', help='File(s) to be geocoded')
    parser.add_argument('-l','--lookup', dest='lookup_file', help='Lookup table file generated by InSAR processors.')
    parser.add_argument('-i','--interpolate', dest='method',\
                        choices={'nearest','linear'}, default='nearest',\
                        help='interpolation/resampling method. Default: nearest')
    parser.add_argument('--fill', dest='fill_value', default=np.nan,\
                        help='Value used for points outside of the interpolation domain. Default: np.nan')
    parser.add_argument('--no-parallel',dest='parallel',action='store_false',default=True,\
                        help='Disable parallel processing. Diabled auto for 1 input file.')
    parser.add_argument('-o','--output', dest='outfile', help="output file name. Default: add prefix 'geo_'")

    inps = parser.parse_args()
    return inps


######################################################################################
def main(argv):
    inps = cmdLineParse()
    inps.file = ut.get_file_list(inps.file)
    print 'number of files to geocode: '+str(len(inps.file))
    print inps.file
    print 'interpolation method: '+inps.method
    print 'fill_value: '+str(inps.fill_value)

    # check outfile and parallel option
    if inps.parallel:
        num_cores, inps.parallel, Parallel, delayed = ut.check_parallel(len(inps.file))

    #####
    if len(inps.file) == 1:
        geocode_file_with_geo_lut(inps.file[0], inps.lookup_file, inps.method, inps.fill_value, inps.outfile)
    elif inps.parallel:
        Parallel(n_jobs=num_cores)(delayed(geocode_file_with_geo_lut)\
                                   (fname, inps.lookup_file, inps.method, inps.fill_value) for fname in inps.file)
    else:
        for fname in inps.file:
            geocode_file_with_geo_lut(fname, inps.lookup_file, inps.method, inps.fill_value)

    print 'Done.'
    return

######################################################################################
if __name__ == '__main__':
    main(sys.argv[1:])

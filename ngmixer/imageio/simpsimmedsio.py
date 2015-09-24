#!/usr/bin/env python
from __future__ import print_function
import os
import numpy
import logging
import copy
import fitsio

# meds and ngmix imports
import meds

# local imports
from .medsio import MEDSImageIO
from ..defaults import LOGGERNAME, PSF_IND_FIELD, PSF_IM_FIELD

# logging
log = logging.getLogger(LOGGERNAME)

class SimpSimMEDSImageIO(MEDSImageIO):
    def _set_defaults(self):
        super(SimpSimMEDSImageIO, self)._set_defaults()
        self.conf['psf_ind_field'] = self.conf.get('psf_ind_field',PSF_IND_FIELD)
        self.conf['psf_im_field'] = self.conf.get('psf_im_field',PSF_IM_FIELD)

    def get_file_meta_data(self):
        meds_meta_list = self.meds_meta_list
        dt = meds_meta_list[0].dtype.descr

        if 'config_file' in self.conf:
            tmp,config_file = os.path.split(self.conf['config_file'])
            clen=len(config_file)
            dt += [('ngmixer_config','S%d' % clen)]

        flen=max([len(mf) for mf in self.meds_files_full] )
        dt += [('meds_file','S%d' % flen)]

        nband=len(self.meds_files_full)
        meta=numpy.zeros(nband, dtype=dt)

        for band in xrange(nband):
            meds_file = self.meds_files_full[band]
            meds_meta=meds_meta_list[band]
            mnames=meta.dtype.names
            for name in meds_meta.dtype.names:
                if name in mnames:
                    meta[name][band] = meds_meta[name][0]

            if 'config_file' in self.conf:
                meta['ngmixer_config'][band] = config_file
            meta['meds_file'][band] = meds_file

        return meta

    def _load_psf_data(self):
        if 'psf_file' in self.extra_data:
            self.psf_file = self.extra_data['psf_file']
        else:
            pth,bname = os.path.split(self.meds_files_full[0])
            bname = bname.replace('meds','psf')
            self.psf_file = os.path.join(pth,bname)
        log.info('psf file: %s' % self.psf_file)
        self.psf_data = fitsio.read(self.psf_file)    
    
    def _get_psf_image(self, band, mindex, icut):
        """
        Get an image representing the psf
        """

        meds=self.meds_list[band]
        psf_ind_field=self.conf['psf_ind_field']

        ind_psf = meds[psf_ind_field][mindex,icut]

        psf_im_field=self.conf['psf_im_field']
        im = self.psf_data[psf_im_field][ind_psf].copy()
        im /= im.sum()
        cen = numpy.zeros(2)
        cen[0] = meds['cutout_row'][mindex,icut]
        cen[1] = meds['cutout_col'][mindex,icut]
        sigma_pix = 2.5
        
        return im, cen, sigma_pix, self.psf_file


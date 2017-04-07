"""
simple MEDS simulation file for WFIRST image sims
"""
from __future__ import print_function
import os
import numpy
import copy
import fitsio

# meds and ngmix imports
import meds

# local imports
from .medsio import MEDSImageIO

class WFIRSTSimMEDSImageIO(MEDSImageIO):
    def _set_defaults(self):
        super(WFIRSTSimMEDSImageIO, self)._set_defaults()


    def _load_psf_data(self):
        if not self.conf['psfs_in_file']:
            if 'psf_file' in self.extra_data:
                self.psf_file = self.extra_data['psf_file']
            else:
                pth,bname = os.path.split(self.meds_files_full[0])
                bname = bname.replace('meds','psf')
                self.psf_file = os.path.join(pth,bname)
            print('psf file: %s' % self.psf_file)
            self.psf_data = fitsio.read(self.psf_file)


    def _get_psf_image(self, band, mindex, icut):
        """
        Get an image representing the psf
        """
        im = self.meds_list[band].get_psf(mindex,icut)
        pfile = self.meds_files[band]

        im /= im.sum()
        cen = ( numpy.array(im.shape) - 1.0)/2.0
        sigma_pix = 2.5
            
        return im, cen, sigma_pix, pfile

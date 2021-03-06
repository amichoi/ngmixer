#!/usr/bin/env python
from __future__ import print_function
import os
import numpy
import copy
import fitsio

from .medsio import MEDSImageIO
from .. import nbrsfofs
from ..util import print_with_verbosity, \
    interpolate_image, \
    radec_to_unitvecs_ruv, \
    radec_to_thetaphi, \
    thetaphi_to_unitvecs_ruv, \
    MissingDataError

import meds

from pprint import pprint

# flagging
IMAGE_FLAGS_SET=2**0
PSF_IN_BLACKLIST=2**1
PSF_MISSING_S2N=2**2
PSF_LOW_S2N=2**3

# SVMEDS
class SVDESMEDSImageIO(MEDSImageIO):

    def __init__(self, *args, **kw):
        conf = args[0]

        conf['use_psf_rerun'] = conf.get('use_psf_rerun',False)
        conf['center_psf'] = conf.get('center_psf',False)

        if conf['use_psf_rerun']:
            rerun=conf['psf_rerun_version']
            self._load_psfex_blacklist(rerun)

        if 'psf_s2n_checks' in conf:
            self._load_psf_s2n(conf)

        super(SVDESMEDSImageIO,self).__init__(*args, **kw)

        self._load_image_metadata()

    def _load_image_metadata(self):
        """
        tiling was not saved an any existing DES MEDS files,
        so extract it if needed

        Other missing metadata can also be checked for here
        and loaded.  We should cache this
        """
        get_extra_meta=False

        self._image_metadata={}
        self.conf['tilings'] = self.conf.get('tilings',None)

        if self.conf['tilings'] is not None:
            get_extra_meta=True

        if get_extra_meta:
            print("    getting extra image metadata")
            desdata=os.environ['DESDATA']
            meds_desdata=self.meds_list[0]._meta['DESDATA'][0]

            for band in self.iband:

                bmeta={}
                ii=self.meds_list[band].get_image_info()
                meds_meta=self.meds_list[band].get_meta()
                se_ext=meds_meta['se_hdu'][0]-1
                coadd_ext=meds_meta['coadd_hdu'][0]-1

                band_meta=[]
                for i in xrange(ii.size):
                    path=ii['image_path'][i]
                    path=path.replace(meds_desdata,desdata)
                    print("    %d/%d  %s" % (i+1,ii.size,path))

                    if i==0:
                        ext=coadd_ext
                    else:
                        ext=se_ext
                    h=fitsio.read_header(path, ext=ext)

                    band_meta.append(h)
                self._image_metadata[band] = band_meta

    '''
    def _get_offchip_nbr_psf_obs_and_jac(self,band,cen_ind,cen_mindex,cen_obs,nbr_ind,nbr_mindex,nbrs_obs_list):
        assert False,'        FIXME: off-chip nbr %d for cen %d' % (nbr_ind+1,cen_ind+1)
        return None,None
    '''

    def get_file_meta_data(self):
        meds_meta_list = self.meds_meta_list
        dt = meds_meta_list[0].dtype.descr

        if 'config_file' in self.conf:
            tmp,config_file = os.path.split(self.conf['config_file'])
            clen=len(config_file)
            dt += [('ngmixer_config','S%d' % clen)]

        flen=max([len(mf.replace(os.environ['DESDATA'],'${DESDATA}')) for mf in self.meds_files_full] )
        dt += [('meds_file','S%d' % flen)]

        mydesdata = os.environ['DESDATA']
        dt += [('ngmixer_DESDATA','S%d' % len(mydesdata))]

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
            meta['meds_file'][band] = meds_file.replace(os.environ['DESDATA'],'${DESDATA}')
            meta['ngmixer_DESDATA'][band] = mydesdata

        return meta

    def _get_image_flags(self, band, mindex):
        """
        find images associated with the object and get the image flags
        Also add in the psfex flags, eventually incorporated into meds
        """
        meds=self.meds_list[band]
        ncutout=meds['ncutout'][mindex]

        file_ids = meds['file_id'][mindex, 0:ncutout]
        image_flags = self.all_image_flags[band][file_ids]

        return image_flags

    def _get_meds_orig_filename(self, meds, mindex, icut):
        """
        Get the original filename
        """
        file_id=meds['file_id'][mindex, icut]
        ii=meds.get_image_info()
        return ii['image_path'][file_id]

    def get_meta_data_dtype(self):
        dt = super(SVDESMEDSImageIO, self).get_meta_data_dtype()
        rlen = len(self.meds_files_full[0]\
                       .replace(os.environ['DESDATA'],'${DESDATA}')\
                       .split('/')[3])
        dt += [('coadd_run','S%d' % rlen)]
        return dt

    def _get_multi_band_observations(self, mindex):
        coadd_mb_obs_list, mb_obs_list = super(SVDESMEDSImageIO, self)._get_multi_band_observations(mindex)
        run = self.meds_files_full[0]\
            .replace(os.environ['DESDATA'],'${DESDATA}')\
            .split('/')[3]
        coadd_mb_obs_list.meta['meta_data']['coadd_run'] = run
        mb_obs_list.meta['meta_data']['coadd_run'] = run
        return coadd_mb_obs_list, mb_obs_list

    def _should_use_obs(self, band, mindex, icut):

        use=super(SVDESMEDSImageIO,self)._should_use_obs(band, mindex, icut)
        if use and icut > 0:

            tilings=self.conf.get('tilings',None)

            if tilings is not None:
                meds=self.meds_list[band]
                file_id=meds['file_id'][mindex, icut]
                tiling = self._image_metadata[band][file_id]['tiling']
                if tiling not in tilings:
                    #print("        image tiling:",tiling,
                    #      "not in requested tilings",tilings)
                    use=False

        return use

    def _get_band_observations(self, band, mindex):
        coadd_obs_list, obs_list = super(SVDESMEDSImageIO, self)._get_band_observations(band,mindex)
        
        # divide by jacobian scale^2 in order to apply zero-points correctly
        for olist in [coadd_obs_list,obs_list]:
            for obs in olist:
                if obs.meta['flags'] == 0:
                    pixel_scale2 = obs.jacobian.get_det()
                    pixel_scale4 = pixel_scale2*pixel_scale2
                    obs.image /= pixel_scale2
                    obs.weight *= pixel_scale4
                    if obs.weight_raw is not None:
                        obs.weight_raw *= pixel_scale4
                    if obs.weight_us is not None:
                        obs.weight_us *= pixel_scale4

        return coadd_obs_list, obs_list

    def get_epoch_meta_data_dtype(self):
        dt = super(SVDESMEDSImageIO, self).get_epoch_meta_data_dtype()
        dt += [('image_id','i8')]  # image_id specified in meds creation, e.g. for image table
        return dt

    def _fill_obs_meta_data(self,obs, band, mindex, icut):
        """
        fill meta data to be included in output files
        """
        super(SVDESMEDSImageIO, self)._fill_obs_meta_data(obs, band, mindex, icut)
        meds=self.meds_list[band]
        file_id  = meds['file_id'][mindex,icut].astype('i4')
        image_id = meds._image_info[file_id]['image_id']
        obs.meta['meta_data']['image_id'][0]  = image_id

    def _load_psf_data(self):
        self.psfex_lists = self._get_psfex_lists()

    def _get_psf_image(self, band, mindex, icut):
        """
        Get an image representing the psf
        """

        meds=self.meds_list[band]
        file_id=meds['file_id'][mindex,icut]

        pex=self.psfex_lists[band][file_id]
        self.psfname=os.path.basename(pex['filename'])
        #if icut > 0:
        #    if self.psfname[0:17] != self.imname[0:17]:
        #        raise RuntimeError("im and psf mismatch: %s "
        #                           "vs %s" % (self.psfname,self.imname))

        row=meds['orig_row'][mindex,icut]
        col=meds['orig_col'][mindex,icut]

        if self.conf['center_psf']:
            use_row,use_col=round(row),round(col)
        else:
            use_row,use_col=row,col

        im=pex.get_rec(use_row,use_col)
        cen=pex.get_center(use_row,use_col)

        im=im.astype('f8', copy=False)

        sigma_pix=pex.get_sigma()

        if 'trim_psf' in self.conf and icut > 0:
            im,cen=self._trim_psf(im, cen)

        return im, cen, sigma_pix, pex['filename']

    def _trim_psf(self, im, cen):
        dims=self.conf['trim_psf']['dims']

        rowstart=int(cen[0]-dims[0]/2.0+0.5)
        rowend=int(cen[0]+dims[0]/2.0+0.5)

        colstart=int(cen[1]-dims[1]/2.0+0.5)
        colend=int(cen[1]+dims[1]/2.0+0.5)

        newim = im[rowstart:rowend, colstart:colend]
        newcen=cen.copy()
        newcen[0]=cen[0]-rowstart
        newcen[1]=cen[1]-rowstart

        '''
        print("Trimming psf to:",dims)
        print("new center:",newcen)
        w=numpy.where(newim == 0.0)
        print("number of zeros:",w[0].size)
        '''

        return newim, newcen

    def _get_blacklist_dir(self):
        """
        location for DES black lists
        """
        dir='$DESDATA/EXTRA/blacklists'
        return os.path.expandvars(dir)

    def _get_psfex_blacklist_file(self, rerun):
        """
        location of DES psfex blacklists for reruns outside
        of DESDM
        """
        dir=self._get_blacklist_dir()
        fname='psfex-%s.txt' % rerun
        return os.path.join(dir,fname)

    def _get_psfex_blacklist_key(self, run, expname, ccd):
        """
        this is our unique key into the blacklist
        """
        key='%s-%s-%02d' % (run,expname,ccd)
        return key

    def _load_psfex_blacklist(self, rerun):
        """
        each psfex rerun has an associated blacklist file
        in a standard location.  Read this and make
        a dictionary keyed by the image metadata
        """
        fname=self._get_psfex_blacklist_file(rerun)
        print("loading psfex blacklist from:",fname)

        blacklist={}
        with open(fname) as fobj:
            for line in fobj:
                data=line.strip().split()

                run     = data[0]
                expname = data[1]
                ccd     = int(data[2])
                flags   = int(data[3])

                key=self._get_psfex_blacklist_key(run, expname, ccd)

                blacklist[key] = flags

        self._psfex_blacklist=blacklist

    def _load_psf_s2n(self, conf):
        fname=conf['psf_s2n_checks']['file']
        print("loading psf s/n:",fname)
        self._psf_s2n = fitsio.read(fname)

    def _get_psfex_lists(self):
        """
        Load psfex objects for each of the SE images
        include the coadd so we get  the index right
        """
        print('loading psfex')
        desdata=os.environ['DESDATA']
        meds_desdata=self.meds_list[0]._meta['DESDATA'][0]

        psfex_lists=[]
        for band in self.iband:
            meds=self.meds_list[band]

            psfex_list = self._get_psfex_objects(meds,band)
            psfex_lists.append( psfex_list )

        return psfex_lists

    def _psfex_path_from_image_path(self, meds, image_path):
        """
        infer the psfex path from the image path.
        """
        desdata=os.environ['DESDATA']
        meds_desdata=meds._meta['DESDATA'][0]

        psfpath=image_path.replace('.fits.fz','_psfcat.psf')
        if desdata not in psfpath:
            psfpath=psfpath.replace(meds_desdata,desdata)

        if self.conf['use_psf_rerun'] and 'coadd' not in psfpath:
            psfparts=psfpath.split('/')
            psfparts[-6] = 'EXTRA' # replace 'OPS'
            psfparts[-3] = 'psfex-rerun/%s' % self.conf['psf_rerun_version'] # replace 'red'
            psfpath='/'.join(psfparts)

        return psfpath

    def _get_psfex_object(self, psfpath):
        """
        read a single PSFEx object
        """
        from psfex import PSFExError, PSFEx
        flags=0
        pex=None
        if self.conf['use_psf_rerun'] and 'coadd' not in psfpath:
            # in Mike's reruns, sometimes the files are corrupted or missing,
            # but these should all be in the blacklist
            fs=psfpath.split('/')
            run=fs[-5]
            expname=fs[-2]
            bname=fs[-1]
            bs=bname.split('_')
            ccd=int(bs[2])

            key=self._get_psfex_blacklist_key(run, expname, ccd)

            if key in self._psfex_blacklist:
                print("   psfex in blacklist, flagging:",psfpath)
                flags |= PSF_IN_BLACKLIST

            if flags == 0 and 'psf_s2n_checks' in self.conf:
                pc=self.conf['psf_s2n_checks']
                pkey=self._psf_s2n['key']
                w,=numpy.where(key==pkey)
                if w.size == 0:
                    print("   psfex bad s2n, flagging:",psfpath)
                    flags |= PSF_MISSING_S2N
                else:
                    s2n_key=pc['key']
                    s2n=self._psf_s2n[s2n_key][w]
                    if s2n < pc['s2n_min']:
                        print("   psfex %s %g < %g" % (s2n_key,s2n,pc['s2n_min']))
                        flags |= PSF_LOW_S2N

        if flags == 0:
            # we expect a well-formed, existing file if there are no flags set
            if not os.path.exists(psfpath):
                raise MissingDataError("missing psfex: %s" % psfpath)
            else:
                print_with_verbosity("loading: %s" % psfpath,verbosity=2)
                try:
                    pex=PSFEx(psfpath)
                except PSFExError as err:
                    raise MissingDataError("problem with psfex file "
                                           "'%s': %s " % (psfpath,str(err)))
        return pex, flags

    def _get_psfex_objects(self, meds, band):
        """
        Load psfex objects for all images
        """

        psfex_list=[]

        info=meds.get_image_info()
        nimage=info.size
        nflagged=0
        for i in xrange(nimage):
            pex=None

            # don't even bother if we are going to skip this image
            flags = self.all_image_flags[band][i]

            if (i==0) and not self.conf['fit_coadd_galaxy']:
                print("skipping coadd psf")
                self.all_image_flags[band][i] |= 1
            else:
                if (flags & self.conf['image_flags2check']) == 0:

                    impath=info['image_path'][i].strip()
                    psfpath = self._psfex_path_from_image_path(meds, impath)

                    # pex might be None with flags set
                    pex, psf_flags = self._get_psfex_object(psfpath)
                    if psf_flags != 0:
                        self.all_image_flags[band][i] |= psf_flags
                        nflagged += 1


            psfex_list.append(pex)

        print("    flagged %d/%d psfex for band %s" % (nflagged,nimage,band))
        return psfex_list

    def _get_replacement_flags(self, filenames):
        from .util import CombinedImageFlags

        if not hasattr(self,'_replacement_flags'):
            fname=os.path.expandvars(self.conf['replacement_flags'])
            print("reading replacement flags: %s" % fname)
            self._replacement_flags=CombinedImageFlags(fname)

        default=self.conf['image_flags2check']
        return self._replacement_flags.get_flags_multi(filenames,default=default)

    def _load_meds_files(self):
        """
        Load all listed meds files
        We check the flags indicated by image_flags2check.  the saved
        flags are 0 or IMAGE_FLAGS_SET
        """

        self.meds_list=[]
        self.meds_meta_list=[]
        self.all_image_flags=[]

        for i,funexp in enumerate(self.meds_files):
            f = os.path.expandvars(funexp)
            print('band %d meds: %s' % (i,f))
            medsi=meds.MEDS(f)
            medsi_meta=medsi.get_meta()
            image_info=medsi.get_image_info()

            if i==0:
                nobj_tot=medsi.size
            else:
                nobj=medsi.size
                if nobj != nobj_tot:
                    raise ValueError("mismatch in meds "
                                     "sizes: %d/%d" % (nobj_tot,nobj))
            self.meds_list.append(medsi)
            self.meds_meta_list.append(medsi_meta)
            image_flags=image_info['image_flags'].astype('i8')

            if 'replacement_flags' in self.conf and self.conf['replacement_flags'] is not None and image_flags.size > 1:
                print("    replacing image flags")
                image_flags[1:] = \
                    self._get_replacement_flags(image_info['image_path'][1:])

            # now we reduce the flags to zero or IMAGE_FLAGS_SET
            # copy out and check image flags just for cutouts
            cimage_flags=image_flags[1:].copy()
            w,=numpy.where( (cimage_flags & self.conf['image_flags2check']) != 0)
            print("    flags set for: %d/%d" % (w.size,cimage_flags.size))
            cimage_flags[:] = 0
            if w.size > 0:
                cimage_flags[w] = IMAGE_FLAGS_SET

            # copy back in reduced flags
            image_flags[1:] = cimage_flags
            self.all_image_flags.append(image_flags)

        self.nobj_tot = self.meds_list[0].size

# SV multifit with one-off WCS
class MOFSVDESMEDSImageIO(SVDESMEDSImageIO):
    def __init__(self,*args,**kwargs):
        super(MOFSVDESMEDSImageIO,self).__init__(*args,**kwargs)

        read_wcs = self.conf.get('read_wcs',False)
        if read_wcs:
            self.wcs_transforms = self._get_wcs_transforms()

    def _get_wcs_transforms(self):
        """
        Load the WCS transforms for each meds file
        """
        import json
        from esutil.wcsutil import WCS

        print('loading WCS')
        wcs_transforms = {}
        for band in self.iband:
            mname = self.conf['meds_files_full'][band]
            wcsname = mname.replace('-meds-','-meds-wcs-').replace('.fits.fz','.fits').replace('.fits','.json')
            print('loading: %s' % wcsname)
            try:
                with open(wcsname,'r') as fp:
                    wcs_list = json.load(fp)
            except:
                assert False,"WCS file '%s' cannot be read!" % wcsname

            wcs_transforms[band] = []
            for hdr in wcs_list:
                wcs_transforms[band].append(WCS(hdr))

        return wcs_transforms

class Y1DESMEDSImageIO(SVDESMEDSImageIO):
    def __init__(self,*args,**kwargs):
        super(Y1DESMEDSImageIO,self).__init__(*args,**kwargs)

        self._load_wcs_data()

    def _set_defaults(self):
        super(Y1DESMEDSImageIO,self)._set_defaults()
        self.conf['read_me_wcs'] = self.conf.get('read_me_wcs',False)
        self.conf['prop_sat_starpix'] = self.conf.get('prop_sat_starpix',False)
        self.conf['flag_y1_stellarhalo_masked'] = self.conf.get('flag_y1_stellarhalo_masked',False)

    def _load_wcs_data(self):
        # should we read from the original file?
        read_wcs = self.conf.get('read_wcs',False)
        if read_wcs:
            self._load_wcs_from_files()
        else:
            self._load_wcs_from_meds()

    def _load_wcs_from_meds(self):
        from esutil.wcsutil import WCS
        import json

        print('loading WCS from meds')
        wcs_transforms = {}
        for band in self.iband:
            wcs_transforms[band] = {}

            info = self.meds_list[band].get_image_info()
            nimage = info.size

            # get coadd file ID            
            # a total hack, but should work!
            # assumes all objects from the same coadd!
            coadd_file_id = numpy.max(numpy.unique(self.meds_list[band]['file_id'][:,0]))
            assert coadd_file_id >= 0,"Could not get coadd_file_id from MEDS file!"

            wcs_dict = json.loads( info['wcs'][0] )
            wcs_transforms[band][coadd_file_id] = WCS(wcs_dict)

            for i in xrange(nimage):
                if i != coadd_file_id:

                    wcs_dict = json.loads( info['wcs'][i] )
                    wcs_transforms[band][i] = WCS(wcs_dict)

        self.wcs_transforms = wcs_transforms

    def _load_wcs_from_files(self):
        """
        Load the WCS transforms for each meds file
        """
        from esutil.wcsutil import WCS

        print('loading WCS from original files')
        wcs_transforms = {}
        for band in self.iband:
            wcs_transforms[band] = {}

            info = self.meds_list[band].get_image_info()
            nimage = info.size
            meta = self.meds_meta_list[band]

            # get coadd file ID            
            # a total hack, but should work!
            # assumes all objects from the same coadd!
            coadd_file_id = numpy.max(numpy.unique(self.meds_list[band]['file_id'][:,0]))
            assert coadd_file_id >= 0,"Could not get coadd_file_id from MEDS file!"
            
            # in image header for coadd
            coadd_path = info['image_path'][coadd_file_id].strip()
            coadd_path = coadd_path.replace(meta['DESDATA'][0],'${DESDATA}')

            if os.path.exists(os.path.expandvars(coadd_path)):
                h = fitsio.read_header(os.path.expandvars(coadd_path),ext=1)
                wcs_transforms[band][coadd_file_id] = WCS(h)
            else:
                wcs_transforms[band][coadd_file_id] = None
                print("warning: missing coadd WCS from image: %s" % coadd_path)

            # in scamp head files for SE
            if self.conf['read_me_wcs']:
                scamp_dir = os.path.join('/'.join(coadd_path.split('/')[:-2]),'QA/coadd_astrorefine_head')
                for i in xrange(nimage):
                    if i != coadd_file_id:
                        scamp_name = os.path.basename(info['image_path'][i].strip()).replace('.fits.fz','.head')
                        scamp_file = os.path.join(scamp_dir,scamp_name)

                        if os.path.exists(os.path.expandvars(scamp_file)):
                            h = fitsio.read_scamp_head(os.path.expandvars(scamp_file))
                            wcs_transforms[band][i] = WCS(h)
                        else:
                            wcs_transforms[band][i] = None
                            print("warning: missing scamp head: %s" % scamp_file)

        self.wcs_transforms = wcs_transforms

    def _get_offchip_nbr_psf_obs_and_jac(self,band,cen_ind,cen_mindex,cen_obs,nbr_ind,nbr_mindex,nbrs_obs_list):
        """
        how this works...

        Simple Version (below):

            1) use coadd WCS to get offset of nbr from central in u,v
            2) use the Jacobian of the central to turn offset in u,v to row,col
            3) return central PSF and nbr's Jacobian
                return cen_obs.get_psf(),J_nbr

        Complicated Version (to do!):

            1) find a fiducial point on the chip where the galaxy's flux falls (either via its pixels in the
               coadd seg map or some other means)
            2) compute Jacobian and PSF model about this point from the SE WCS and PSF models
            3) use the offset in u,v from the fiducial point to the location of the nbr plus the offset in
               pixels of the fiducial point from the central to center the Jacobian properly on the chip
            4) return the new PSF observation and new Jacobian

        NOTE: We don't fit the PSF observation here. The job of this class is to just to prep observations
        for fitting!
        """
        
        # hack for nbrs with no data!
        # FIXME - need to flag these when being read in maybe?
        if self.meds_list[band]['ncutout'][nbr_mindex] == 0:
            return None,None

        # 1) use coadd WCS to get offset in u,v
        # 1a) first get coadd WCS
        assert self.meds_list[band]['file_id'][cen_mindex,0] == \
          self.meds_list[band]['file_id'][nbr_mindex,0], \
          "central and nbr have different coadd file IDs when getting off-chip WCS! cen file_id = %d, nbr file_id = %d"\
          % (self.meds_list[band]['file_id'][cen_mindex,0],self.meds_list[band]['file_id'][nbr_mindex,0])
        coadd_wcs = self.wcs_transforms[band][self.meds_list[band]['file_id'][cen_mindex,0]]

        # 1b) now get positions
        row_cen = self.meds_list[band]['orig_row'][cen_mindex,0]
        col_cen = self.meds_list[band]['orig_col'][cen_mindex,0]
        ra_cen,dec_cen = coadd_wcs.image2sky(col_cen+1.0,row_cen+1.0) # reversed for esutil WCS objects!

        row_nbr = self.meds_list[band]['orig_row'][nbr_mindex,0]
        col_nbr = self.meds_list[band]['orig_col'][nbr_mindex,0]
        ra_nbr,dec_nbr = coadd_wcs.image2sky(col_nbr+1.0,row_nbr+1.0) # reversed for esutil WCS objects!

        # 1c) now get u,v offset
        # FIXME - discuss projection with Mike and Erin
        # right now using vector to point where rhat of nbr hits the tangent plane of the central
        # differs in length from unity by 1/cos(angle between central and nbr)
        # this is also a *tiny* effect!
        rhat_cen,uhat_cen,vhat_cen = radec_to_unitvecs_ruv(ra_cen,dec_cen)
        rhat_nbr,uhat_nbr,vhat_nbr = radec_to_unitvecs_ruv(ra_nbr,dec_nbr)
        cosang = numpy.dot(rhat_cen,rhat_nbr)
        u_nbr = numpy.dot(rhat_nbr,uhat_cen)/cosang/numpy.pi*180.0*60.0*60.0 # arcsec
        v_nbr = numpy.dot(rhat_nbr,vhat_cen)/cosang/numpy.pi*180.0*60.0*60.0 # arcsec
        uv_nbr = numpy.array([u_nbr,v_nbr])

        # 2) use the Jacobian of the central to turn offset in u,v to row,col
        # Jacobian is used like this
        # (u,v) = J x (row-row0,col-col0)
        # so (row,col) of nbr is
        #   (row,col)_nbr = J^(-1) x (u,v) + (row0,col0)
        J = cen_obs.get_jacobian()
        Jinv = numpy.linalg.inv([[J.dudrow,J.dudcol],[J.dvdrow,J.dvdcol]])
        row0,col0 = J.get_cen()
        rowcol_nbr = numpy.dot(Jinv,uv_nbr) + numpy.array([row0,col0])

        # 2a) now get new Jacobian
        J_nbr = J.copy() # or whatever
        J_nbr.set_cen(row=rowcol_nbr[0],col=rowcol_nbr[1])

        # 3) return it!
        print('        did off-chip nbr %d for cen %d:' % (nbr_ind+1,cen_ind+1))
        print('            band,cen_icut:     ',band,cen_obs.meta['icut'])
        print('            u,v nbr:           ',uv_nbr)
        print('            r,c nbr:           ',rowcol_nbr)
        print('            box_size - r,c nbr:',self.meds_list[band]['box_size'][nbr_mindex]- rowcol_nbr)
        return cen_obs.get_psf(),J_nbr

    def _interpolate_maskbits(self,iobj,m1,icutout1,m2,icutout2):
        rowcen1 = m1['cutout_row'][iobj,icutout1]
        colcen1 = m1['cutout_col'][iobj,icutout1]
        jacob1 = m1.get_jacobian_matrix(iobj,icutout1)
        
        rowcen2 = m2['cutout_row'][iobj,icutout2]
        colcen2 = m2['cutout_col'][iobj,icutout2]
        jacob2 = m2.get_jacobian_matrix(iobj,icutout2)
        
        im1 = m1.get_cutout(iobj,icutout1,type='bmask')
        
        msk = numpy.array([2048+1024+512+256+128+16+8+1],dtype='u4')
        
        q = numpy.where( ((im1&2 != 0) | (im1&4 != 0)) 
                         & 
                         (im1&32 != 0) 
                         &
                         (im1&msk == 0))
        im1[:,:] = 0
        im1[q] = 1
        
        assert m1['box_size'][iobj] == m2['box_size'][iobj]
        assert m1['id'][iobj] == m2['id'][iobj]

        return interpolate_image(rowcen1, colcen1, jacob1, im1, 
                                 rowcen2, colcen2, jacob2)[0]
    
    def _get_extra_bitmasks(self,coadd_mb_obs_list,mb_obs_list):        
        marr = self.meds_list
        mindex = mb_obs_list.meta['meds_index']
        
        bmasks = []
        for bandt,mt in enumerate(marr):
            bmask = numpy.zeros((mt['box_size'][mindex],mt['box_size'][mindex])).astype('i4')
            
            # do the coadd
            if len(coadd_mb_obs_list[bandt]) > 0 and coadd_mb_obs_list[bandt][0].meta['flags'] == 0:
                bmask |= mt.get_cutout(mindex,0,type='bmask')
            
            # do each band
            for band,obs_list in enumerate(mb_obs_list):
                for obs in obs_list:
                    if obs.meta['flags'] == 0:                        
                        bmaski = self._interpolate_maskbits(mindex,
                                                            marr[band],
                                                            obs.meta['icut'],
                                                            mt,
                                                            0)
                        bmask |= bmaski
    
            bmasks.append(bmask)
            
        return bmasks

    def _expand_mask(self,bmask,rounds=1):
        cbmask = bmask.copy()
        
        qx_prev,qy_prev = numpy.where(cbmask != 0)
        
        for r in xrange(rounds):
            qx = []
            qy = []
            for ix,iy in zip(qx_prev,qy_prev):
                for dx in [-1,0,1]:
                    iix = ix + dx
                    if iix >= 0 and iix < bmask.shape[0]:
                        for dy in [-1,0,1]:
                            iiy = iy + dy
                            if iiy >= 0 and iiy < bmask.shape[1]:
                                cbmask[iix,iiy] = 1
                                qx.append(iix)
                                qy.append(iiy)
                                
            qx_prev = numpy.array(qx)
            qy_prev = numpy.array(qy)
                            
        return cbmask

    def _prop_extra_bitmasks(self, bmasks, mb_obs_list):
        mindex = mb_obs_list.meta['meds_index']
            
        # interp to each image
        for band,obs_list in enumerate(mb_obs_list):
            m = self.meds_list[band]
            bmask = bmasks[band]
            
            for obs in obs_list:
                if obs.meta['flags'] == 0:
                    # interp
                    icut = obs.meta['icut']
                    
                    rowcen1 = m['cutout_row'][mindex,0]
                    colcen1 = m['cutout_col'][mindex,0]
                    jacob1 = m.get_jacobian_matrix(mindex,0)
                    
                    rowcen2 = m['cutout_row'][mindex,icut]
                    colcen2 = m['cutout_col'][mindex,icut]
                    jacob2 = m.get_jacobian_matrix(mindex,icut)
                    
                    bmaski = interpolate_image(rowcen1, colcen1, jacob1, bmask,
                                               rowcen2, colcen2, jacob2)[0]
                    
                    """
                    if band == 0 and mb_obs_list.meta['id'] == 3076597980:
                        import matplotlib.pyplot as plt

                        fig,axs = plt.subplots(1,3)
                        axs[0].imshow(bmaski)
                        axs[1].imshow(self._expand_mask(bmaski,rounds=1))
                        axs[2].imshow(self._expand_mask(bmaski,rounds=2))
                        
                        import ipdb
                        ipdb.set_trace()
                    """
                    
                    bmaski = self._expand_mask(bmaski,rounds=2)
                    
                    # now set weights to zero
                    q = numpy.where((bmaski != 0) & (obs.seg == 0))
                    if len(q[0]) > 0:
                        print('    masked %d pixels due to saturation in any band' % q[0].size)
                        if hasattr(obs,'weight_raw'):
                            obs.weight_raw[q] = 0.0
                            
                        if hasattr(obs,'weight_us'):
                            obs.weight_us[q] = 0.0
                            
                        if hasattr(obs,'weight'):
                            obs.weight[q] = 0.0
                            
                        if hasattr(obs,'weight_orig'):
                            obs.weight_orig[q] = 0.0        

    def _flag_y1_stellarhalo_masked_one(self,mb_obs_list):
        mindex = mb_obs_list.meta['meds_index']
        seg_number = self.meds_list[0]['number'][mindex]
        
        assert mb_obs_list.meta['id'] == self.meds_list[0]['id'][mindex], \
            "Problem getting meds index! check value of mb_obs_list.meta['meds_index']"
        
        flags = 0
        for band,obs_list in enumerate(mb_obs_list):
            for obs in obs_list:
                if obs.meta['flags'] == 0:

                    icut = obs.meta['icut']
                    bmask = self.meds_list[band].get_cutout(mindex,icut,type='bmask')
                    
                    q = numpy.where((bmask&32 != 0) & (obs.seg == seg_number))
                    
                    # debugging code - leave for now
                    """
                    if numpy.any(bmask&32 != 0) and q[0].size > 0:
                        import matplotlib.pyplot as plt
                        
                        qq = numpy.where(bmask&32 != 0)
                        pim = numpy.zeros_like(bmask)
                        pim[qq] = 1
                        
                        pseg = obs.seg.copy()
                        pseg = pseg.astype('f8')
                        useg = numpy.sort(numpy.unique(obs.seg))
                        nuseg = float(len(useg))
                        for i,sval in enumerate(useg):
                            qq = numpy.where(obs.seg == sval)
                            if qq[0].size > 0:
                                pseg[qq] = float(i)/nuseg
                                
                        fig,axs = plt.subplots(1,2)
                        axs[0].imshow(pim)                                                
                        axs[1].imshow(pseg)
                        
                        import ipdb
                        ipdb.set_trace()
                    """
                    
                    if q[0].size > 0:                        
                        flags = 1
                        return flags
                    
        return flags
    
    def _flag_y1_stellarhalo_masked(self,coadd_mb_obs_list,mb_obs_list):
        flags = 0
        flags |= self._flag_y1_stellarhalo_masked_one(coadd_mb_obs_list)
        if flags == 0:
            flags |= self._flag_y1_stellarhalo_masked_one(mb_obs_list)
            
        return flags

    def _get_multi_band_observations(self, mindex):
        coadd_mb_obs_list, mb_obs_list = super(Y1DESMEDSImageIO, self)._get_multi_band_observations(mindex)
        
        # mask extra pixels in saturated stars
        if self.conf['prop_sat_starpix']:
            # get total OR'ed bit mask
            bmasks = self._get_extra_bitmasks(coadd_mb_obs_list,mb_obs_list)
            self._prop_extra_bitmasks(bmasks,mb_obs_list)

        # flag things where seg map touches a stellar halo as defined by DESDM
        if self.conf['flag_y1_stellarhalo_masked']:            
            flags = self._flag_y1_stellarhalo_masked(coadd_mb_obs_list,mb_obs_list)
            if flags != 0:
                print('    flagged object due to seg map touching masked stellar halo')
                coadd_mb_obs_list.meta['obj_flags'] |= flags
                mb_obs_list.meta['obj_flags'] |= flags

        return coadd_mb_obs_list, mb_obs_list

class Y3DESMEDSImageIO(Y1DESMEDSImageIO):
    """
    this is using Brian Yanny's exposure pattern list format
    """
    def __init__(self, *args, **kw):
        self._load_psf_map(**kw)
        super(Y3DESMEDSImageIO,self).__init__(*args, **kw)


    def _load_psf_map(self, **kw):
        """
        we fake the coadd psf
        """
        extra_data=kw.get('extra_data',{})

        map_file=extra_data.get('psf_map',None)
        if map_file is None:
            raise RuntimeError("for Y3 you must send a map file")

        print("reading psf map:",map_file)
        psf_map={}
        with open(map_file) as fobj:
            for line in fobj:

                pattern=line.strip()

                bname=os.path.basename(pattern)

                # bname looks like D00149774_g_c%02d_r2382p01_psfexcat.psf
                # we will key off the exposure name, e.g. D00149774
                fs = bname.split('_')
                expname = fs[0]

                full_pattern = os.path.join('$DESDATA', 'OPS', 'finalcut', pattern)
                psf_map[expname] = full_pattern

        self._psf_map=psf_map

    def _psfex_path_from_image_path(self, meds, image_path):
        """
        infer the psfex path from the image path.
        """

        bname = os.path.basename(image_path)
        # these bnames look like DES0157-3914_r2577p01_D00490381_r_c48_nwgint.fits
        # we need the exposure name, e.g. D00490381 and the ccd number, e.g. 48

        fs = bname.split('_')

        expname = fs[2]
        ccd = int( fs[4][1:] )

        psf_path = self._psf_map[expname] % ccd


        psf_path = os.path.expandvars(psf_path)
        return psf_path

    def get_epoch_meta_data_dtype(self):
        dt = super(SVDESMEDSImageIO, self).get_epoch_meta_data_dtype()
        dt += [('image_id','S49')]  # image_id specified in meds creation, e.g. for image table
        return dt

class Y3DESMEDSImageIOAlt(Y1DESMEDSImageIO):
    """
    This is the original, with the explicit filename-psf path mapping
    """
    def __init__(self, *args, **kw):
        self._load_psf_map(**kw)
        super(Y3DESMEDSImageIO,self).__init__(*args, **kw)


    def _load_psf_map(self, **kw):
        """
        we fake the coadd psf
        """
        extra_data=kw.get('extra_data',{})

        map_file=extra_data.get('psf_map',None)
        if map_file is None:
            raise RuntimeError("for Y3 you must send a map file")

        data=fitsio.read(map_file)
        psf_map={}
        for i in xrange(data.size):

            if i==0:
                ii=i+1
            else:
                ii=i

            fname=data['im_filename'][i].strip()
            psf_path = data['psf_local_path'][ii].strip()

            keep = fname.split('_')[0:0+3]
            key = '-'.join(keep )

            psf_map[key] = psf_path

        self._psf_map=psf_map

    def _psfex_path_from_image_path(self, meds, image_path):
        """
        infer the psfex path from the image path.
        """

        fname = os.path.basename(image_path).replace('.fits.fz','.fits')

        fs = fname.split('_')
        key = '-'.join( fs[2:2+3] )
        psfpath = self._psf_map[key]

        psfpath = os.path.expandvars(psfpath)
        return psfpath

    def get_epoch_meta_data_dtype(self):
        dt = super(SVDESMEDSImageIO, self).get_epoch_meta_data_dtype()
        dt += [('image_id','S49')]  # image_id specified in meds creation, e.g. for image table
        return dt



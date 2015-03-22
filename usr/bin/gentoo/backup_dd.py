# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
# - restore with dd:
#   gunzip -c /mnt/sda1/hda.img.gz | dd of=/dev/hda bs=64K
# ====================================================================
import datetime
import os
import pylon.base

dd_image_name = 'dd.img.gz'

class backup_dd(pylon.base.base):
    'implement backups using dd'

    def do(self, src_path, dest_path, opts=''):
        img_path = os.path.join(dest_path, dd_image_name)

        self.ui.info('Saving {0} to {1}...'.format(src_path, dest_path))
        self.dispatch('dd ' +

                      # Above 32k/64k/128k (depending on machine)
                      # block sizes there is nothing to be gained
                      # since the blocks have to be split up.
                      'bs=64k ' +

                      # if read errors occur during image creation,
                      # these options allow to read the input device
                      # without stopping
                      #'conv=sync,noerror ' +

                      # add additional options
                      opts + ' ' +

                      # paths
                      'if=' + src_path + ' | gzip -c > ' + img_path,
                      output='both')

    def info(self, src_path, dest_path, opts=''):
        img_path = os.path.join(dest_path, dd_image_name)
        img_date = datetime.datetime.fromtimestamp(os.path.getmtime(img_path)).isoformat()

        self.ui.info('Timestamp of last image for {0}: {1}'.format(src_path, img_date))

    def modify(self, src_path, dest_path, opts=''):
        pass

# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
import os
import pylon.base

class backup_partclone(pylon.base.base):
    'implement efficient partition cloning module.'

    def do(self, src_path, dest_path, opts=''):

        # - determine partition from UUID in src_path
        partition = self.dispatch('findfs UUID={0}'.format(src_path), passive=True, output=None).stdout[0]
    
        self.ui.info('Saving {0} to {1}...'.format(partition, dest_path))
        # FIXME determine verbosity of -d option
        self.dispatch('partclone.{0} -c -d -s {1} -o {2}'.format(opts, partition, dest_path),
                      output='both')
        
    def info(self, src_path, dest_path, opts=''):
        self.ui.info('Showing partclone image header for {0}...'.format(dest_path))
        self.dispatch('partclone.info {0}'.format(dest_path),
                      output='both')

    def modify(self, src_path, dest_path, opts=''):
        pass

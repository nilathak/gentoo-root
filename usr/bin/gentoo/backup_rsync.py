# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
import pylon.base

class backup_rsync(pylon.base.base):
    'implement simple rsync copy backup module.'

    def do(self, src_path, dest_path, opts=''):
        self.ui.info('Saving {0} to {1}...'.format(src_path, dest_path))
        self.dispatch('rsync ' +
                      # preserve almost everything
                      '--archive ' +

                      # preserve hard links too
                      '--hard-links ' +

                      # preserve sparse files
                      '--sparse ' +

                      # NFS backup destination benefits from this
                      '--omit-dir-times ' +

                      # omit filesystems mounted within src
                      '--one-file-system ' +

                      # reuse partially transmitted files (eg after
                      # user break)
                      '--partial ' +

                      # delete unwanted files on destination
                      '--delete-during ' +

                      # add additional rsync options
                      opts + ' ' +

                      # paths
                      src_path + ' ' + dest_path,
                      output='both')
        self.ui.info('Saved {0} to {1}'.format(src_path, dest_path))

    def info(self, src_path, dest_path, opts=''):
        self.ui.info('Differences {0} <-> {1}...'.format(src_path, dest_path))
        self.dispatch('rsync ' +
                      # preserve almost everything
                      '--archive ' +

                      # preserve hard links too
                      '--hard-links ' +

                      # preserve sparse files
                      '--sparse ' +

                      # NFS backup destination benefits from this
                      '--omit-dir-times ' +

                      # omit filesystems mounted within src
                      '--one-file-system ' +

                      # reuse partially transmitted files (eg after
                      # user break)
                      '--partial ' +

                      # delete unwanted files on destination
                      '--delete-during ' +

                      # verbose & read-only
                      '--verbose --dry-run ' +

                      # add additional rsync options
                      opts + ' ' +

                      # paths
                      src_path + ' ' + dest_path,
                      output='both')

    def modify(self, src_path, dest_path, opts=''):
        pass

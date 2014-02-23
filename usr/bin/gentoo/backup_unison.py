# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
import pylon.base

class backup_unison(pylon.base.base):
    'implement semi-automated unison synchronization'

    def do(self, src_path, dest_path, opts=''):
        self.ui.info('Synchronizing %s to %s...' % (src_path, dest_path))
        try:
            self.dispatch('unison-2.32 ' +

                          # paths
                          src_path + ' ' + dest_path + ' '

                          # avoid verbose startup
                          '-contactquietly ' +

                          # synchronize modtimes, owner & group properties
                          '-times -owner -group ' +

                          # FIXME maybe to allow easy user mapping on mephisto
                          #'-numericids ' +

                          # be quiet
                          '-terse ' +

                          # add additional options
                          opts,
                          output='both')
        except self.exc_class as e:
            if e.owner.ret_val == 1:
                self.ui.warning('Some files were skipped, maybe conflicts occured!')
            else:
                raise e

    def info(self, src_path, dest_path, opts=''):
        # unison itself does not allow to see differences all at once via the
        # console interface. if a sync is not successful, two backups
        # are available at all times, so trust unison...
        pass

    def modify(self, src_path, dest_path, opts=''):
        pass

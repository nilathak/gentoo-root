import pylon.base as base

class backup_unison(base.base):
    'implement semi-automated unison synchronization'

    def do(self, src_path, dest_path, opts=''):
        self.ui.info('Synchronizing {0} to {1}...'.format(src_path, dest_path))
        try:
            self.dispatch('eselect unison update && unison ' +

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

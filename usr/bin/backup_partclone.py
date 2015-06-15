'''
implement efficient partition cloning module

Clonezilla uses partclone as default, partimage/ntfsclone are optional.
Basically partclone.ntfs and ntfsclone are the same. Both of them are based on the libntfs. However, partclone.ntfs has some improvements:
- CRC checking info is stored.
- TUI (Terminal User Interface) output is available for partclone.
- partclone can do partition clone directly, eg partclone.ntfs -b -s /dev/sda1 -O /dev/sdb1. With ntfsclone you have to pipe.
- More messages are shown when running partclone.
'''
    
import os
import pylon.base as base
import sys

class backup_partclone(base.base):

    __doc__ = sys.modules[__name__].__doc__
    
    def do(self, src_path, dest_path, opts=''):

        # - determine partition from UUID in src_path
        partition = self.dispatch('findfs UUID={0}'.format(src_path), passive=True, output=None).stdout[0]
    
        self.ui.info('Saving {0} to {1}...'.format(partition, dest_path))
        self.dispatch('partclone.{0} -c -s {1} -O {2}'.format(opts, partition, dest_path),
                      output='both')
        self.ui.info('Saved {0} to {1}'.format(partition, dest_path))
        
    def info(self, src_path, dest_path, opts=''):
        self.ui.info('Showing partclone image header for {0}...'.format(dest_path))
        self.dispatch('partclone.info {0}'.format(dest_path),
                      output='both')

    def modify(self, src_path, dest_path, opts=''):
        pass

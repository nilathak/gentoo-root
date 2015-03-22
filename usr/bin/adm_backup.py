#!/usr/bin/env python3
# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================

transfer_engines = (
    'btrfs',
    #'dd',        # deprecated, FIXME replac by ntfsclone
    #'rdiff',     # deprecated, replaced by btrfs
    'rsync',
    'unison',
    )

auto_tasks = {
    'diablo': (
        ('/mnt/work/projects/backup/cache/diablo',
         '/mnt/work/projects/backup/cache',
         'btrfs', '10h10d'),
        #('/mnt/work/projects/backup/cache/diablo',
        # '/mnt/work/projects/backup/pool',
        # 'btrfs', '6m1y'),

        #('/mnt/work/projects/backup/pool/games',
        # '/mnt/work/projects/backup/pool',
        # 'btrfs', '6m'),
        #('/mnt/work/projects/backup/pool/video',
        # '/mnt/work/projects/backup/pool',
        # 'btrfs', '6m'),

        # external backup
        ('/mnt/work/projects/backup/cache/diablo.2*',
         '/mnt/work/projects/backup',
         'btrfs', ''),
        #('/mnt/work/projects/backup/pool/diablo*',
        # '/mnt/work/projects/backup/extpool',
        # 'btrfs', 'clone'),
    ),
    
    }

manual_tasks = {
    'diablo': (
        # unison example
        #('/mnt/video/',
        # '/tmp/backup/video/unison/',
        # 'unison', '-batch -ignore "Path movies" -ignore "Path 0_sort"'),
        ),
    }

import os
import gentoo.job
import gentoo.ui
import pylon.base

class ui(gentoo.ui.ui):
    def __init__(self, owner):
        super().__init__(owner)

        self.parser_common.add_argument('-s','--src',
                                        help='do not loop all tasks, specify src of single task')
        self.parser_common.add_argument('-e','--engine',
                                        help='use a specific backup engine')
        self.init_op_parser()
        self.parser_modify.add_argument('-o','--options',
                                        help='pass custom string to backup module')

    def setup(self):
        super().setup()
        if not self.args.op:
            raise self.owner.exc_class('Specify at least one subcommand operation')
        if self.args.engine and self.args.engine not in transfer_engines:
            raise self.owner.exc_class('unknown backup engine ' + self.args.engine)
        
class adm_backup(pylon.base.base):
    'container script for all backup related admin tasks'

    def run_core(self):
        # initialize enabled backup engines
        for engine in transfer_engines:
            module = getattr(__import__('gentoo.backup_' + engine), 'backup_' + engine)
            setattr(self, engine, getattr(module, 'backup_' + engine)(owner=self))

        import datetime
        t1 = datetime.datetime.now()
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()
        self.ui.info(self.ui.args.op + ' took ' + str(datetime.datetime.now() - t1) + ' to complete...')

    def do(self, src_path, dest_path, opts, cmd):

        # lock backup dest to prevent overlapping backup
        import hashlib
        lock_path = '/tmp/' + self.__class__.__name__ + hashlib.md5(src_path + dest_path).hexdigest()
        try:
            os.makedirs(lock_path)
        except OSError:
            raise self.exc_class('backup to {0} is already locked'.format(dest_path))

        # remove lock dir in every case
        try:
            cmd(src_path, dest_path, opts)
        finally:
            os.rmdir(lock_path)

    def selected(self, engine, src):
        return ((not self.ui.args.engine or self.ui.args.engine == engine) and
                (not self.ui.args.src    or self.ui.args.src == src))
            
    def adm_backup_pre(self):
        'perform host-specific preprocessing'
        if hasattr(self, self.ui.hostname + '_pre'):
            getattr(self, self.ui.hostname + '_pre')()

    def adm_backup_post(self):
        'perform host-specific postprocessing'
        if hasattr(self, self.ui.hostname + '_post'):
            getattr(self, self.ui.hostname + '_post')()

    def adm_backup_auto(self):
        'perform host-specific automatic tasks'
        for (src, dest, engine, opts) in auto_tasks[self.ui.hostname]:
            if (self.selected(engine, src)):
                self.dispatch(lambda src=src,dest=dest,opts=opts,engine=engine:
                              self.do(src, dest, opts, getattr(self, engine).do),
                              blocking=False)
        self.join()

    def adm_backup_manual(self):
        'perform host-specific manual tasks'
        for (src, dest, engine, opts) in manual_tasks[self.ui.hostname]:
            if (self.selected(engine, src)):
                self.dispatch(lambda src=src,dest=dest,opts=opts,engine=engine:
                              self.do(src, dest, opts, getattr(self, engine).do),
                              blocking=False)
        self.join()

    def adm_backup_info(self):
        'show generic info about tasks'
        tasks = list(auto_tasks[self.ui.hostname])
        tasks.extend(manual_tasks[self.ui.hostname])
        for (src, dest, engine, opts) in tasks:
            if (self.selected(engine, src)):
                self.do(src, dest, opts, getattr(self, engine).info)

    def adm_backup_modify(self):
        'modify specified tasks'
        tasks = list(auto_tasks[self.ui.hostname])
        tasks.extend(manual_tasks[self.ui.hostname])
        for (src, dest, engine, opts) in tasks:
            if (self.selected(engine, src)):
                self.do(src, dest, opts, getattr(self, engine).modify)

    def adm_backup_list(self):
        'display list of configured backup tasks'
        import pprint
        self.ui.info('Automatic tasks:')
        pprint.pprint(auto_tasks)
        self.ui.info('Manual tasks:')
        pprint.pprint(manual_tasks)

    # FIXME
    #def baal_pre(self):
    #    self.ui.debug('Mounting LUKS backup drive...')
    #    self.dispatch('cryptsetup luksOpen /dev/sdh1 backup',
    #                  output='both')
    #    self.dispatch('mount /dev/mapper/backup /media/backup',
    #                  output='both')
    #    try:
    #        self.dispatch('mkdir /tmp/backup',
    #                      output=None)
    #    except self.exc_class:
    #        pass
    #    self.dispatch('mount -o bind /media/backup/data /tmp/backup',
    #                  output='both')
    # 
    #def baal_post(self):
    #    self.ui.debug('Unmounting LUKS backup drive...')
    #    self.dispatch('umount /tmp/backup',
    #                  output='both')
    #    self.dispatch('umount /media/backup',
    #                  output='both')
    #    self.dispatch('cryptsetup luksClose backup',
    #                  output='both')

if __name__ == '__main__':
    app = adm_backup(job_class=gentoo.job.job,
                     ui_class=ui)
    app.run()

#!/usr/bin/env python
# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================

# module settings
rsync_excludes = '--exclude="/dev/" --exclude="/lost+found/" --exclude="/proc/" --exclude="/sys/" --exclude="/tmp/" --exclude="/var/tmp/"'

transfer_engines = (
    'dd',
    'git',       # deprecated, not feasable
    'rdiff',     # will be replaced by snapshot facility of btrfs
    'rsync',
    'snapshot',  # deprecated
    'unison',
    )

auto_tasks = {
    'baal': (
        #('/mnt/audio/',
        # '/mnt/work/projects/backup/audio/rdiff/',
        # 'rdiff', ''),
        #('/mnt/docs/',
        # '/mnt/work/projects/backup/docs/rdiff/',
        # 'rdiff', ''),
        #('/mnt/games/',
        # '/mnt/work/projects/backup/games/rdiff/',
        # 'rdiff', ''),
        #('/mnt/images/',
        # '/mnt/work/projects/backup/images/rdiff/',
        # 'rdiff', ''),
        #('/mnt/software/',
        # '/mnt/work/projects/backup/software/rdiff/',
        # 'rdiff', ''),
        #('/mnt/video/',
        # '/mnt/work/projects/backup/video/rdiff/',
        # 'rdiff', '--exclude="/mnt/video/0_sort/" --exclude="/mnt/video/movies/" --exclude="/mnt/video/porn/" --exclude="/mnt/video/series/"'),
        #('/mnt/work/',
        # '/mnt/work/projects/backup/work/rdiff/',
        # 'rdiff', '--exclude="/mnt/work/projects/backup/"'),

        # enabling these will blow the inode limit on the raid
        # ext3 system. once again -> wait for btrfs snapshots
        #('/mnt/work/projects/backup/baal/rsync/',
        # '/mnt/work/projects/backup/baal/rdiff/',
        # 'rdiff', ''),
        #('/mnt/work/projects/backup/diablo/rsync/',
        # '/mnt/work/projects/backup/diablo/rdiff/',
        # 'rdiff', ''),

        # simplify! wait for decent snapshot filesystem...
        #('/mnt/work/projects/backup/diablo_win/dd/',
        # '/mnt/work/projects/backup/diablo_win/rdiff/',
        # 'rdiff', ''),
        #('/mnt/work/projects/backup/mephisto_win/dd/',
        # '/mnt/work/projects/backup/mephisto_win/rdiff/',
        # 'rdiff', ''),

        ('/',
         '/mnt/work/projects/backup/baal/rsync/',
         'rsync', rsync_excludes),
        ),

    'diablo': (
        #('/dev/sda3',
        # '/mnt/work/projects/backup/diablo_win/dd/',
        # 'dd', ''),
        ('/',
         '/mnt/work/projects/backup/diablo/rsync/',
         'rsync', rsync_excludes),
        ),

    }

manual_tasks = {
    'baal': (
        ('/mnt/audio/',
         '/tmp/backup/audio/unison/',
         'unison', '-batch'),
        #('/mnt/docs/',
        # '/tmp/backup/docs/unison/',
        # 'unison', '-batch'),
        ('/mnt/games/',
         '/tmp/backup/games/unison/',
         'unison', '-batch'),
        #('/mnt/images/',
        # '/tmp/backup/images/unison/',
        # 'unison', '-batch'),
        ('/mnt/software/',
         '/tmp/backup/software/unison/',
         'unison', '-batch'),
        ('/mnt/video/',
          '/tmp/backup/video/unison/',
         'unison', '-batch -ignore "Path movies" -ignore "Path 0_sort"'),
        #('/mnt/work/',
        # '/tmp/backup/work/unison/',
        # 'unison', '-batch -ignore "Path projects/backup"'),

        # export baal system (for remote backup purposes)
        ('/mnt/work/projects/backup/baal/rsync/',
         '/tmp/backup/baal/rsync/',
         'rsync', ''),

        # export diablo system (for remote backup purposes)
        #('/mnt/work/projects/backup/diablo_win/dd/',
        # '/tmp/backup/diablo_win/dd/',
        # 'rsync', ''),
        ('/mnt/work/projects/backup/diablo/rsync/',
         '/tmp/backup/diablo/rsync/',
         'rsync', ''),
        ),
    }

# module imports
import os
import gentoo.job
import gentoo.ui
import pylon.base

class ui(gentoo.ui.ui):

    def cleanup(self):
        super(ui, self).cleanup(self.opts.type)

    def configure(self):
        super(ui, self).configure()

        self.parser.add_option('-t','--type', type='string',
                               help=self.extract_doc_strings())
        self.parser.add_option('-s','--src', type='string',
                               help='do not loop all tasks, specify src of single task')
        self.parser.add_option('-e','--engine', type='string',
                               help='use a specific backup engine')
        self.parser.add_option('-o','--options', type='string',
                               default='',
                               help='give additional options')

    def validate(self):
        super(ui, self).validate()
        if (self.opts.type == 'modify' and
            (not self.opts.src or
             self.opts.options == '')):
            raise self.owner.exc_class('modify needs --src & --options')
        if not self.opts.type:
            raise self.owner.exc_class('type spec is mandatory')
        if (self.opts.engine and
            self.opts.engine not in transfer_engines):
            raise self.owner.exc_class('unknown backup engine')

class adm_backup(pylon.base.base):
    'container script for all backup related admin tasks'

    def run_core(self):
        # initialize enabled backup engines
        for engine in transfer_engines:
            module = getattr(__import__('gentoo.backup_' + engine), 'backup_' + engine)
            setattr(self, engine, getattr(module, 'backup_' + engine)(owner=self))

        import datetime
        t1 = datetime.datetime.now()
        getattr(self, self.__class__.__name__ + '_' + self.ui.opts.type)()
        self.ui.info(self.ui.opts.type + ' took ' + str(datetime.datetime.now() - t1) + ' to complete...')

    def lock_dest(self, dest_path):
        # check if another backup process is already active
        lock_path = os.path.normpath(dest_path.rstrip('/') + '.locked')
        if os.path.exists(lock_path):
            raise self.exc_class('destination %s already locked' % dest_path)

        # lock backup dest to prevent overlapping backup
        # processes. race conditions during creation of this dir would
        # surface as exception.
        try:
            os.makedirs(lock_path)
        except Exception:
            raise self.exc_class('perhaps another backup process locked faster?')
        return lock_path

    def unlock_dest(self, lock_path):
        os.rmdir(lock_path)

    def lock(self, src_path, dest_path, opts, cmd):
        lock_path = self.lock_dest(dest_path)
        try:
            cmd(src_path, dest_path, opts)
        finally:
            self.unlock_dest(lock_path)

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
            if ((not self.ui.opts.engine or
                 self.ui.opts.engine == engine) and
                (not self.ui.opts.src or
                 self.ui.opts.src == src)):
                self.dispatch(lambda src=src,dest=dest,opts=opts,engine=engine:
                              self.lock(src, dest, opts, getattr(self, engine).do),
                              blocking=False)
        self.join()

    def adm_backup_info(self):
        'show generic info about tasks'
        tasks = list(auto_tasks[self.ui.hostname])
        tasks.extend(manual_tasks[self.ui.hostname])
        for (src, dest, engine, opts) in tasks:
            if ((not self.ui.opts.engine or
                 self.ui.opts.engine == engine) and
                (not self.ui.opts.src or
                 self.ui.opts.src == src)):
                self.lock(src, dest, opts, getattr(self, engine).info)

    def adm_backup_list(self):
        'display list of configured backup tasks'
        import pprint
        self.ui.info('Automatic tasks:')
        pprint.pprint(auto_tasks)
        self.ui.info('Manual tasks:')
        pprint.pprint(manual_tasks)

    def adm_backup_modify(self):
        'modify specified tasks'
        tasks = list(auto_tasks[self.ui.hostname])
        tasks.extend(manual_tasks[self.ui.hostname])
        for (src, dest, engine, opts) in tasks:
            if ((not self.ui.opts.engine or
                 self.ui.opts.engine == engine) and
                (not self.ui.opts.src or
                 self.ui.opts.src == src)):
                self.lock(src, dest, opts, getattr(self, engine).modify)

    def adm_backup_manual(self):
        'perform host-specific manual tasks'
        for (src, dest, engine, opts) in manual_tasks[self.ui.hostname]:
            if ((not self.ui.opts.engine or
                 self.ui.opts.engine == engine) and
                (not self.ui.opts.src or
                 self.ui.opts.src == src)):
                self.dispatch(lambda src=src,dest=dest,opts=opts,engine=engine:
                              self.lock(src, dest, opts, getattr(self, engine).do),
                              blocking=False)
        self.join()

    def baal_pre(self):
        # backup drive was encrypted as follows:
        # cryptsetup -c aes-xts-plain -y -s 512 luksFormat /dev/sdh1
        # mkfs.ext3 -m0 -I 128 /dev/mapper/backup
        self.ui.debug('Mounting LUKS backup drive...')
        self.dispatch('cryptsetup luksOpen /dev/sdh1 backup',
                      output='both')
        self.dispatch('mount /dev/mapper/backup /media/backup',
                      output='both')
        try:
            self.dispatch('mkdir /tmp/backup',
                          output=None)
        except self.exc_class:
            pass
        self.dispatch('mount -o bind /media/backup/data /tmp/backup',
                      output='both')

    def baal_post(self):
        self.ui.debug('Unmounting LUKS backup drive...')
        self.dispatch('umount /tmp/backup',
                      output='both')
        self.dispatch('umount /media/backup',
                      output='both')
        self.dispatch('cryptsetup luksClose backup',
                      output='both')

if __name__ == '__main__':
    app = adm_backup(job_class=gentoo.job.job,
                     ui_class=ui)
    app.run()

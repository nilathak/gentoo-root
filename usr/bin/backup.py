#!/usr/bin/env python3
import hashlib
import os
import pprint
import pylon.base as base
import pylon.gentoo.job as job
import pylon.gentoo.ui as ui
import re

transfer_engines = (
    'btrfs',
    'partclone',
    'rsync',
    'unison',
    )

tasks = {
    'diablo': (
        ('diablo',
         '/mnt/work/backup/online/diablo',
         '/mnt/work/backup/online',
         'btrfs', '10h'),
        ('diablo_offline',
         '/mnt/work/backup/online/diablo',
         '/mnt/work/backup/offline',
         'btrfs', '10d2m'),
        ('games',
         '/mnt/work/backup/offline/games',
         '/mnt/work/backup/offline',
         'btrfs', '2d2m'),
        ('video',
         '/mnt/work/backup/offline/video',
         '/mnt/work/backup/offline',
         'btrfs', '2d2m'),

        # <mount via KDE>
        # backup.py exec -t external --mail && admin.py check_btrfs -o external --mail && umount /run/media/schweizer/external && hdparm -y `findfs UUID=afd6243b-2580-4509-8ac2-b8c5702d6212`
        ('diablo_external',
         '/mnt/work/backup/online/diablo',
         '/run/media/schweizer/external',
         'btrfs', '1h10y4'), # add hour interval to allow easy manual refresh at any time
        ('games_external',
         '/mnt/work/backup/offline/games',
         '/run/media/schweizer/external',
         'btrfs', '1h6m'),
        ('video_external',
         '/mnt/work/backup/offline/video',
         '/run/media/schweizer/external',
         'btrfs', '1h6m'),

        #('02282962282955C7',
        # '/run/media/schweizer/external/win7.img',
        # 'partclone', 'ntfs'),
        #('/mnt/video/',
        # '/tmp/backup/video/unison/',
        # 'unison', '-batch -ignore "Path movies" -ignore "Path 0_sort"'),
    ),
}

class ui(ui.ui):
    def __init__(self, owner):
        super().__init__(owner)

        self.parser_common.add_argument('-e','--engine',
                                        help='use a specific backup engine')
        self.parser_common.add_argument('-t','--task',
                                        help='do not loop all tasks, specify regex of backup task ids')
        self.init_op_parser()
        self.parser_modify.add_argument('-o','--options',
                                        help='pass custom string to backup module')

    def setup(self):
        super().setup()
        if not self.args.op:
            raise self.owner.exc_class('Specify at least one subcommand operation')
        if self.args.engine and self.args.engine not in transfer_engines:
            raise self.owner.exc_class('unknown backup engine ' + self.args.engine)
        if self.args.task and not list(filter(lambda x: re.search(self.args.task, x[0]), tasks[self.hostname])):
            raise self.owner.exc_class('no matching backup task')
        
class backup(base.base):
    'container script for all backup related admin tasks'

    def run_core(self):
        # initialize enabled backup engines
        for engine in transfer_engines:
            if not self.ui.args.engine or engine == self.ui.args.engine:
                setattr(self, engine, getattr(__import__('backup_' + engine), 'backup_' + engine)(owner=self))
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()

    def do(self, task, src_path, dest_path, opts, command):

        # lock backup dest to prevent overlapping backup
        lock_path = '/tmp/' + self.__class__.__name__ + hashlib.md5(task.encode('utf-8')).hexdigest()
        try:
            os.makedirs(lock_path)
        except OSError:
            raise self.exc_class('backup task {0} is already locked'.format(task))

        # remove lock dir in every case
        try:
            command(task, src_path, dest_path, opts)
        finally:
            os.rmdir(lock_path)

    def do_loop(self, command, blocking):
        for (task, src_path, dest_path, engine, opts) in tasks[self.ui.hostname]:
            if (self.selected(engine, task)):
                self.dispatch(self.do,
                              blocking=blocking,
                              task=task,
                              src_path=src_path,
                              dest_path=dest_path,
                              opts=opts,
                              command=getattr(getattr(self, engine), command))
            
    def selected(self, engine, task):
        return ((not self.ui.args.engine or self.ui.args.engine == engine) and
                (not self.ui.args.task or re.search(self.ui.args.task, task)))
            
    @ui.log_exec_time
    def backup_exec(self):
        'perform host-specific tasks'
        self.do_loop('do', False)
        self.join()

    @ui.log_exec_time
    def backup_info(self):
        'show generic info about tasks'
        self.do_loop('info', True)

    @ui.log_exec_time
    def backup_modify(self):
        'modify specified tasks'
        self.do_loop('modify', True)

    @ui.log_exec_time
    def backup_list(self):
        'display list of configured backup tasks'
        self.ui.info('Backup tasks:')
        pprint.pprint(tasks)

if __name__ == '__main__':
    app = backup(job_class=job.job,
                 ui_class=ui)
    app.run()

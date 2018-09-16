#!/usr/bin/env python3
'''container script for all backup related admin tasks
'''
import hashlib
import os
import pprint
import pylon.base
import pylon.gentoo.job
import pylon.gentoo.ui
import re
import sys

transfer_engines = (
    'btrfs',
    'unison',
    )

tasks = {
    'diablo': (
        # BTRFS restore from total online failure
        # - restore any snapshot, does not need to be latest
        #   btrfs send <root_date> | btrfs receive <online>
        # - create a new writeable root (old root_date does not need to be child of root, -p will recognize it a as correct ref during next auto backup)
        #   btrfs sub snap <root_date> <root>
        # - backup script should take it from here => ensure with dry_run
        #   at least this has been test manually
        #   btrfs sub snap -r <root> <root_now>
        #   btrfs send -p <root_date> <root_now> | btrfs receive <offline>
        # - offline & external are not related => always restore from external, even if offline has survived, otherwise external reflinks are lost.
        #   manually apply external <->  latest-offline diff to new root, then simply restart incremental backups on offline
        
        ('diablo',
         '/mnt/work/backup/online/diablo',
         '/mnt/work/backup/online',
         'btrfs', '10h'),
        ('diablo_offline',
         '/mnt/work/backup/online/diablo',
         '/mnt/work/backup/offline',
         'btrfs', '10d2m'),

        # <mount via KDE>
        # killall -s SIGHUP smartd && backup.py exec -t external --mail && admin.py check_btrfs -o external --mail && umount /run/media/schweizer/external && cryptsetup close /dev/mapper/luks-3c196e96-d46c-4a9c-9583-b79c707678fc && hdparm -y `findfs UUID=3c196e96-d46c-4a9c-9583-b79c707678fc`
        ('diablo_external',
         '/mnt/work/backup/online/diablo',
         '/run/media/schweizer/external',
         'btrfs', 'a15y4'), # add hour interval to allow easy manual refresh at any time

        #('/mnt/video/',
        # '/tmp/backup/video/unison/',
        # 'unison', '-batch -ignore "Path movies" -ignore "Path 0_sort"'),
    ),
}

class ui(pylon.gentoo.ui.ui):
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
        if self.args.task and not any(True for x in tasks[self.hostname] if re.search(self.args.task, x[0])):
            raise self.owner.exc_class('no matching backup task')
        
class backup(pylon.base.base):
    __doc__ = sys.modules[__name__].__doc__

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
            
    @pylon.log_exec_time
    def backup_exec(self):
        'perform host-specific tasks'
        self.do_loop('do', False)
        self.join()

    @pylon.log_exec_time
    def backup_info(self):
        'show generic info about tasks'
        self.do_loop('info', True)

    @pylon.log_exec_time
    def backup_modify(self):
        'modify specified tasks'
        self.do_loop('modify', True)

    @pylon.log_exec_time
    def backup_list(self):
        'display list of configured backup tasks'
        self.ui.info('Backup tasks:')
        pprint.pprint(tasks)

if __name__ == '__main__':
    app = backup(job_class=pylon.gentoo.job.job,
                 ui_class=ui)
    app.run()

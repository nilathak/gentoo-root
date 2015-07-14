#!/usr/bin/env python3
import hashlib
import os
import pprint
import pylon.base as base
import pylon.gentoo.job as job
import pylon.gentoo.ui as ui

transfer_engines = (
    'btrfs',
    #'rdiff',     # deprecated, replaced by btrfs
    'partclone',
    'rsync',
    'unison',
    )

auto_tasks = {
    'diablo': (
        ('/mnt/work/projects/backup/cache/diablo',
         '/mnt/work/projects/backup/cache',
         'btrfs', '10h10d'),
         
        ('/mnt/work/projects/backup/cache/diablo',
         '/mnt/work/projects/backup/pool',
         'btrfs', '2d1m'),
        ('/mnt/work/projects/backup/pool/games',
         '/mnt/work/projects/backup/pool',
         'btrfs', '2d1m'),
        ('/mnt/work/projects/backup/pool/video',
         '/mnt/work/projects/backup/pool',
         'btrfs', '2d1m'),
    ),
}

manual_tasks = {
    'diablo': (
        ('/mnt/work/projects/backup/cache/diablo',
         '/run/media/schweizer/extpool',
         'btrfs', '1h6m4y'), # add hour interval to allow easy manual refresh at any time
        ('/mnt/work/projects/backup/pool/games',
         '/run/media/schweizer/extpool',
         'btrfs', '1h6m4y'),
        ('/mnt/work/projects/backup/pool/video',
         '/run/media/schweizer/extpool',
         'btrfs', '1h6m4y'),

        ('02282962282955C7',
         '/run/media/schweizer/extpool/win7.img',
         'partclone', 'ntfs'),

        # unison example
        #('/mnt/video/',
        # '/tmp/backup/video/unison/',
        # 'unison', '-batch -ignore "Path movies" -ignore "Path 0_sort"'),
    ),
}

class ui(ui.ui):
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
        
class backup(base.base):
    'container script for all backup related admin tasks'

    def run_core(self):
        # initialize enabled backup engines
        for engine in transfer_engines:
            if not self.ui.args.engine or engine == self.ui.args.engine:
                setattr(self, engine, getattr(__import__('backup_' + engine), 'backup_' + engine)(owner=self))
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()

    def do(self, src_path, dest_path, opts, cmd):

        # lock backup dest to prevent overlapping backup
        lock_path = '/tmp/' + self.__class__.__name__ + hashlib.md5(src_path.encode('utf-8') + dest_path.encode('utf-8')).hexdigest()
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
            
    @ui.log_exec_time
    def backup_auto(self):
        'perform host-specific automatic tasks'
        for (src, dest, engine, opts) in auto_tasks[self.ui.hostname]:
            if (self.selected(engine, src)):
                self.dispatch(lambda src=src,dest=dest,opts=opts,engine=engine:
                              self.do(src, dest, opts, getattr(self, engine).do),
                              blocking=False)
        self.join()

    @ui.log_exec_time
    def backup_manual(self):
        'perform host-specific manual tasks'
        for (src, dest, engine, opts) in manual_tasks[self.ui.hostname]:
            if (self.selected(engine, src)):
                self.dispatch(lambda src=src,dest=dest,opts=opts,engine=engine:
                              self.do(src, dest, opts, getattr(self, engine).do),
                              blocking=False)
        self.join()

    @ui.log_exec_time
    def backup_info(self):
        'show generic info about tasks'
        tasks = list(auto_tasks[self.ui.hostname])
        tasks.extend(manual_tasks[self.ui.hostname])
        for (src, dest, engine, opts) in tasks:
            if (self.selected(engine, src)):
                self.do(src, dest, opts, getattr(self, engine).info)

    @ui.log_exec_time
    def backup_modify(self):
        'modify specified tasks'
        tasks = list(auto_tasks[self.ui.hostname])
        tasks.extend(manual_tasks[self.ui.hostname])
        for (src, dest, engine, opts) in tasks:
            if (self.selected(engine, src)):
                self.do(src, dest, opts, getattr(self, engine).modify)

    @ui.log_exec_time
    def backup_list(self):
        'display list of configured backup tasks'
        self.ui.info('Automatic tasks:')
        pprint.pprint(auto_tasks)
        self.ui.info('Manual tasks:')
        pprint.pprint(manual_tasks)

if __name__ == '__main__':
    app = backup(job_class=job.job,
                 ui_class=ui)
    app.run()

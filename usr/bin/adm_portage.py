#!/usr/bin/env python3
import copy
import datetime
import os
import pylon.base as base
import pylon.gentoo.job as job
import pylon.gentoo.ui as ui
import stat

emerge_world     = 'emerge --nospinner --autounmask-keep-masks --with-bdeps=y -uDNv world'
emerge_depclean  = 'emerge --depclean'
emerge_pretend   = ' -p'

class ui(ui.ui):
    def __init__(self, owner):
        super().__init__(owner)

        self.parser.add_argument('-o','--options',
                                 default='',
                                 help='add additional emerge option string')
        self.init_op_parser()

    def setup(self):
        super().setup()
        if not self.args.op:
            self.args.op = 'qr'
        
class adm_portage(base.base):
    'container script for all portage related admin tasks'

    def run_core(self):
        t1 = datetime.datetime.now()
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()
        self.ui.info(self.ui.args.op + ' took ' + str(datetime.datetime.now() - t1) + ' to complete...')

    def adm_portage_qr(self):
        'generate quick emerge report on console'
        self.dispatch(emerge_world + emerge_pretend + ' ' + self.ui.args.options,
                      output='nopipes')

    def adm_portage_report(self):
        'report portage state'

        self.ui.info('Syncing portage tree...')
        # =========================================================
        self.dispatch('emaint sync -A',
                      output='stderr')
        
        self.ui.info('Updating eix cache...')
        # =========================================================
        self.dispatch('eix-update',
                      output='stderr')

        self.ui.info('Checking for updates...')
        # =========================================================
        try:
            self.dispatch(emerge_world + emerge_pretend + ' ' + self.ui.args.options)
        except self.exc_class:
            pass

        self.ui.info('Checking for potential vulnerabilities...')
        # =========================================================
        try:
            self.dispatch('glsa-check -ntv all')
        except self.exc_class:
            pass

        self.ui.info('Performing useful emaint commands...')
        # =========================================================
        try:
            self.dispatch('emaint -c all')
        except self.exc_class:
            pass

        self.ui.info('Checking for obsolete dependencies...')
        # =========================================================
        try:
            self.dispatch(emerge_depclean + emerge_pretend)
        except self.exc_class:
            pass

        self.ui.info('Checking for obsolete package.* file entries...')
        # =========================================================
        try:
            self.dispatch('eix-test-obsolete brief')
        except self.exc_class:
            pass

        self.ui.info('Checking for inconsistent passwd/group files...')
        # =========================================================
        try:
            self.dispatch('pwck -qr')
        except self.exc_class:
            pass
        try:
            self.dispatch('grpck -qr')
        except self.exc_class:
            pass

        self.ui.info('Checking for sane system file permissions...')
        # =========================================================
        dir_exceptions = (
            '/dev',
            '/home',
            '/mnt',
            '/proc',
            '/run',
            '/sys',
            '/usr/portage/distfiles',
            '/tmp',
            '/var',
            )
        file_exceptions = (
            )
        if not self.ui.args.dry_run:
            for root, dirs, files in os.walk('/', onerror=lambda x: self.ui.error(str(x))):
                for d in copy.copy(dirs):
                    if os.path.join(root, d) in dir_exceptions:
                        dirs.remove(d)
                for f in copy.copy(files):
                    if os.path.join(root, f) in file_exceptions:
                        files.remove(f)

                for d in dirs:
                    dir = os.path.join(root, d)
                    if (os.stat(dir).st_mode & stat.S_IWGRP or
                        os.stat(dir).st_mode & stat.S_IWOTH):
                        self.ui.warning('Found world/group writeable dir: ' + dir)

                for f in files:
                    try:
                        file = os.path.join(root, f)
                        if (os.stat(file).st_mode & stat.S_IWGRP or
                            os.stat(file).st_mode & stat.S_IWOTH):
                            self.ui.warning('Found world/group writeable file: ' + file)

                        if (os.stat(file).st_mode & stat.S_ISGID or
                            os.stat(file).st_mode & stat.S_ISUID):
                            if (os.stat(file).st_nlink > 1):
                                # someone may try to retain older versions of binaries, eg avoiding security fixes
                                self.ui.warning('Found suid/sgid file with multiple links: ' + file)
                    except Exception as e:
                        # dead links are reported by cruft anyway
                        pass
                        
    def adm_portage_update(self):
        'performs the update'

        self.ui.info('Checking for updates...')
        # =========================================================
        #try:
        self.dispatch(emerge_world + ' --keep-going ' + self.ui.args.options,
                      output='nopipes')

        self.ui.info('Checking for obsolete dependencies...')
        # =========================================================
        self.dispatch(emerge_depclean,
                      output='nopipes')

        self.ui.info('Rebuilding broken lib dependencies...')
        # =========================================================
        # continue if emerge version does not support sets yet
        try:
            self.dispatch('emerge @preserved-rebuild',
                          output='nopipes')
        except self.exc_class:
            pass

        self.ui.info('Checking for obsolete distfiles...')
        # =========================================================
        self.dispatch('eclean -Cd distfiles -f',
                      output='nopipes')

if __name__ == '__main__':
    app = adm_portage(job_class=job.job,
                      ui_class=ui)
    app.run()

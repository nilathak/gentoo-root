#!/usr/bin/env python3
# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================

emerge_world     = 'emerge --nospinner --autounmask-keep-masks --with-bdeps=y -uDNv world'
emerge_depclean  = 'emerge --depclean'
emerge_pretend   = ' -p'

import gentoo.job
import gentoo.ui
import pylon.base

class ui(gentoo.ui.ui):
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
        
class adm_portage(pylon.base.base):
    'container script for all portage related admin tasks'

    def run_core(self):
        import datetime
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

        self.ui.info('Searching for multiple suid/sgid links (someone may try to retain older versions of binaries, eg avoiding security fixes)')
        # =========================================================
        self.dispatch('cd /; find . -xdev -type f \( -perm -004000 -o -perm -002000 \) -links +1 -ls',
                      output='stdout')
        self.ui.info('Searching for world/group writeable dirs')
        # =========================================================
        self.dispatch('cd /; find . -xdev -path "./home" -prune -o -type d \( -perm -2 -o -perm -20 \) | grep -v "^./var" | grep -v "^./home" | grep -v "^./usr/portage" | grep -v "^./tmp" | xargs ls -ldg',
                      output='stdout')
        self.ui.info('Searching for world/group writeable files')
        # =========================================================
        self.dispatch('cd /; find . -xdev -path "./home" -prune -o -type f \( -perm -2 -o -perm -20 \) | grep -v "^./var" | grep -v "^./home" | grep -v "^./usr/portage" | grep -v "^./tmp" | xargs ls -ldg',
                      output='stdout')

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
    app = adm_portage(job_class=gentoo.job.job,
                      ui_class=ui)
    app.run()

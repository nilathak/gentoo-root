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
emerge_world     = 'emerge --nospinner --autounmask-keep-masks -uDNv world'
emerge_depclean  = 'emerge --depclean'
emerge_pretend   = ' -p'

# module imports
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
        self.parser.add_option('-o','--options', type='string',
                               default='',
                               help='add additional emerge option string')

    def validate(self):
        super(ui, self).validate()
        if not self.opts.type:
            self.opts.type = 'quick_report'

class adm_portage(pylon.base.base):
    'container script for all portage related admin tasks'

    def run_core(self):
        import datetime
        t1 = datetime.datetime.now()
        getattr(self, self.__class__.__name__ + '_' + self.ui.opts.type)()
        self.ui.info(self.ui.opts.type + ' took ' + str(datetime.datetime.now() - t1) + ' to complete...')

    def adm_portage_quick_report(self):
        'generate quick emerge report on console'
        self.dispatch(emerge_world + emerge_pretend + ' ' + self.ui.opts.options,
                      output='nopipes')

    def adm_portage_report(self):
        'report portage state'

        self.ui.info('Syncing portage tree...')
        # =========================================================
        self.dispatch('emerge --sync',
                      output='stderr')

        # overlays
        try:
            self.dispatch('layman',
                          output=None)
        except self.exc_class:
            pass
        else:
            self.ui.info('Syncing overlays...')
            self.dispatch('layman --sync ALL',
                          output='stderr')

        self.ui.info('Updating eix cache...')
        # =========================================================
        self.dispatch('eix-update',
                      output='stderr')

        self.ui.info('Checking for updates...')
        # =========================================================
        try:
            self.dispatch(emerge_world + emerge_pretend + ' ' + self.ui.opts.options)
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
            self.dispatch('emaint -c moveinst')
            self.dispatch('emaint -c world')
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
        self.dispatch(emerge_world + ' --keep-going ' + self.ui.opts.options,
                      output='nopipes')
        #except self.exc_class:
        #    for it in range(1,10):
        #        self.ui.info('emerge world has failed ' + str(it) + ' time(s), trying to restart with --skipfirst...')
        #        try:
        #            self.dispatch(emerge_world + ' --skipfirst ' + self.ui.opts.options,
        #                          output='nopipes')
        #        except self.exc_class:
        #            continue
        #        else:
        #            break

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

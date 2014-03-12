#!/usr/bin/env python3
# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
# thoughts about flow
#  - start always with checked out host branch
#  - pull in external changes only into clean working trees (redundant commits are resolved by git rebase)
#  - host MUST NEVER be merged onto master
#  - commit-based cherry picking from host to master tricky (commits containing host+master files need to be split)
#  - thus, directly commit on host and master using restricted file set and interactive commit
#  - rebasing host with remote master ensures a minimal diffset ("superimposing" files are auto-removed if no longer needed)
#  - avoid host branches on central bare repo. GUI development mainly on master files, host branches backup done by host backup
#
# FIXME
#  - AARGH: additions are removed by repo reset (can be savely removed?, how to stash index?) ADD /etc/portage/cruft.d + /opt/portage
#  - setup eclipse with new git clone
#  - install nano as default editor => /etc/env.d/99editor found in cruft report? => add to master branch
#  - sync new PYLON/GENTOO to DMCE + adapt essence_blender.py (opts as common positional argument?)
#  - belial/baal:
#    - add periodic report
#    - replace mercurial with git in world file
# ====================================================================

# module settings
repo_path = '/mnt/Dropbox/work/projects/workspace/gentoo-repo'
hosts = [
    'diablo',
    'baal',
    'belial',
    ]
# always chdir to work-tree to avoid long relative paths when using --git-dir + --work-tree and calling this script from arbitrary directory
git_cmd = 'cd / && git '

# module imports
import os
import gentoo.job
import gentoo.ui
import pylon.base

class ui(gentoo.ui.ui):
    def __init__(self, owner):
        super().__init__(owner)
        self.init_op_parser()
        self.parser_deliver.add_argument('-s', '--skip', action='count', default=0,
                                         help='resume deliver after manual conflict resolution (1=continue after origin/master pull, 2=continue after host rebase)')

    def setup(self):
        super().setup()
        # validate hostname
        if not self.hostname in hosts:
            raise self.owner.exc_class('unknown host ' + self.hostname)

class adm_config(pylon.base.base):
    'manage config files across multiple hosts'

    def run_core(self):
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()

    @pylon.base.memoize
    def host_files(self):
        self.ui.ext_info('Finding host-specific files (host-only + superimposed)...')
        host_files_actual = self.dispatch(git_cmd + 'diff origin/master ' + self.ui.hostname + ' --name-only', output=None, passive=True).stdout
        host_files_expect = self.dispatch(git_cmd + 'diff origin/master ' + self.ui.hostname + ' --name-only --diff-filter=AM', output=None, passive=True).stdout
        host_files_unexpect = set(host_files_actual) - set(host_files_expect)
        if host_files_unexpect:
            raise self.exc_class('unexpected host-specific diff:' + os.linesep + os.linesep.join(sorted(host_files_unexpect)))
        return sorted(host_files_expect)

    @pylon.base.memoize
    def master_files(self):
        self.ui.ext_info('Finding master-specific files (common files)...')
        all_files = self.dispatch(git_cmd + 'ls-files', output=None, passive=True).stdout
        master_files = set(all_files) - set(self.host_files())
        return sorted(master_files)

    def adm_config_deliver(self):
        'sequence for delivering master + host'
        
        import logging
        verbosity = 'stderr'
        if self.ui.logger.getEffectiveLevel() == logging.DEBUG:
            verbosity = 'both'

        if self.ui.args.skip < 1:

            self.ui.info('Delivering host-specific changes...')
            self.ui.debug('Ensure we start with an empty staging area')
            self.dispatch(git_cmd + 'reset', output=verbosity)
            self.ui.debug('Looping interactive commit until error code >0 => no more modifications to stage or we intentionally did not select any')
            try:
                while True:
                    self.dispatch(git_cmd + 'commit --interactive -uno ' + ' '.join(self.host_files()), output='nopipes')
                    if self.ui.args.dry_run:
                        break
            except self.exc_class:
                pass

            self.ui.debug('Creating stash branch for remaining unstaged modifications')
            self.dispatch(git_cmd + 'stash save', output=verbosity)
            self.dispatch(git_cmd + 'stash branch master', output=verbosity)
            self.ui.info('Delivering master-specific changes...')
            self.ui.debug('Ensure we start with an empty staging area')
            self.dispatch(git_cmd + 'reset', output=verbosity)
            try:
                while True:
                    self.dispatch(git_cmd + 'commit --interactive --no-status -uno ' + ' '.join(self.master_files()), output='nopipes')
                    if self.ui.args.dry_run:
                        break
            except self.exc_class:
                pass

            self.ui.debug('Save any unstaged modifications we do not want to commit anywhere yet (host- or master-specific)')
            self.dispatch(git_cmd + 'stash save', output=verbosity)

            try:
                self.ui.debug('Rebasing local master to remote master...')
                self.dispatch(git_cmd + 'rebase --onto origin/master ' + self.ui.hostname, output=verbosity)
            except self.exc_class:
                raise self.exc_class('Automatic rebase of master to remote master failed! Resolve manually: git diff, git add, git rebase --continue, -s')

        if self.ui.args.skip < 2:
            self.ui.debug('Pushing rebased master to remote...')
            self.dispatch(git_cmd + 'push origin master', output=verbosity)

            try:
                self.ui.debug('Rebasing local host to updated remote master...')
                self.dispatch(git_cmd + 'checkout ' + self.ui.hostname, output=verbosity)
                self.dispatch(git_cmd + 'rebase', output=verbosity)
            except self.exc_class:
                raise self.exc_class('Automatic rebase of local host to updated remote master failed! Resolve manually: git diff, git add, git rebase --continue, -ss')

        self.ui.debug('Cleaning up...')
        self.dispatch(git_cmd + 'branch -d master', output=verbosity)
        self.ui.debug('Restoring any stashed changes...')
        self.dispatch(git_cmd + 'stash pop', output=verbosity)

    def adm_config_report(self):
        'generate report'
        self.ui.info('Listing host-specific diff:')
        self.dispatch(git_cmd + 'diff ' + self.ui.hostname + ' --name-status -- ' + ' '.join(self.host_files()), output='both')

        self.ui.info('Listing master-specific diff:')
        self.dispatch(git_cmd + 'diff origin/master --name-status -- ' + ' '.join(self.master_files()), output='both')

        if self.ui.args.mail:
            self.adm_config_md5()

    def list_repo(self):
        return [''.join(('/',x)) for x in self.dispatch(git_cmd + 'ls-files', output=None).stdout]

    def adm_config_list(self):
        'simple list of managed files for the current host'
        for f in self.list_repo():
            print(f)

    def adm_config_md5(self):
        'check if portage md5 equals git-controlled file => can be removed from git'

        # generate custom portage object/pkg map once
        import cruft
        import re
        objects = {}
        pkg_map = {}
        pkg_err = {}

        for pkg in sorted(cruft.vardb.cpv_all()):
            contents = cruft.vardb._dblink(pkg).getcontents()
            pkg_map.update(dict.fromkeys(contents.keys(), pkg))
            objects.update(contents)
     
        repo_files = self.list_repo()
        portage_controlled = set(objects.keys()) & set(repo_files)
     
        # openrc installs user version => MD5 sum always equal (look into ebuild)
        # /etc/conf.d/hostname (sys-apps/openrc-0.11.8)
        for f in portage_controlled:
            (n_passed, n_checked, errs) = cruft.contents_checker._run_checks(cruft.vardb._dblink(pkg_map[f]).getcontents())
            if not [e for e in errs if re.search(f + '.*MD5', e)]:
                self.ui.warning('=MD5 %s (%s)' % (f, pkg_map[f]))
            else:
                self.ui.debug('!MD5 %s (%s)' % (f, pkg_map[f]))
        
if __name__ == '__main__':
    app = adm_config(job_class=gentoo.job.job,
                      ui_class=ui)
    app.run()

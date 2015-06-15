#!/usr/bin/env python3
'''
Manage config files across multiple hosts

HOWTO
- start always with checked out host branch
- ADDING TO MASTER: git add and deliver_master will work, but be careful to commit host-specific ADDs to host branch
- if MD5 sums are equal and file is not needed anymore: checkin any pending changes, git rm in respective working tree,
- after deliver_master has been executed on one host, use git pull to see changes on other host
- host MUST NEVER be merged onto master; PREFERABLY operate on master directly, or just cherry-pick from host to master:
  - commit-based cherry picking from host to master tricky (commits containing host+master files need to be split)
  git stash && git checkout master
  git log diablo
  (git show <commit hashes>)
  git cherry-pick <commit hashes>
  git push && git checkout diablo && git rebase && git branch -d master && git stash pop
- rebasing host with remote master ensures a minimal diffset ("superimposing" files are auto-removed if no longer needed)
- avoid host branches on central bare repo. GUI development mainly on master files, host branches backup done by host backup

TODO

KEEP JUST FOR HOST/MASTER distinction
- create master_export
  - copy master files to local master repo (followed by slow manual commit without changing files on host)
- create master_import
  - revert master files (check if identical to local master repo)
  - import master from github

- howto easily move a default file from master to specific host branches? authorized_keys?
- MASTER ADD
- DIABLO ADD
  - cruft(2015-03-21 14:27:49,138) ERROR: net-wireless/hostapd-2.3: /etc/hostapd/hostapd.wpa_psk has incorrect MD5sum
  - cruft(2015-03-21 14:28:20,970) ERROR: sys-apps/smartmontools-6.3: /etc/smartd.conf has incorrect MD5sum
    DEVICESCAN -a -n standby,q -I 194 -W 0,0,40 -m root@localhost -C 197+ -U 198+
  - cruft(2015-03-21 14:29:46,330) ERROR: www-servers/apache-2.2.29: /etc/conf.d/apache2 has incorrect MD5sum (REALLY NEEDED???????)
- BELIAL ADD
'''

import functools
import logging
import os
import pylon.base as base
import pylon.gentoo.job as job
import pylon.gentoo.ui as ui
import sys

hosts = [
    'diablo',
    'belial',
    ]

# always chdir to work-tree to avoid long relative paths when using --git-dir + --work-tree and calling this script from arbitrary directory
git_cmd = 'cd / && git '

class ui(ui.ui):
    def __init__(self, owner):
        super().__init__(owner)
        self.init_op_parser()
        self.parser_deliver_master.add_argument('-s', '--skip', action='count', default=0,
                                                help='resume deliver after manual conflict resolution (s=continue after origin/master pull, ss=continue after host rebase)')

    def setup(self):
        super().setup()
        if not self.args.op:
            raise self.owner.exc_class('Specify at least one subcommand operation')
        if self.hostname not in hosts:
            raise self.owner.exc_class('unknown host ' + self.hostname)

class adm_config(base.base):

    __doc__ = sys.modules[__name__].__doc__
    
    def run_core(self):
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()

    @functools.lru_cache(typed=True)
    def host_files(self):
        self.ui.ext_info('Finding host-specific files (host-only + superimposed)...')
        host_files_actual = self.dispatch(git_cmd + 'diff origin/master ' + self.ui.hostname + ' --name-only', output=None, passive=True).stdout
        # host branches can only modify files from master branch or add new ones
        host_files_expect = self.dispatch(git_cmd + 'diff origin/master ' + self.ui.hostname + ' --name-only --diff-filter=AM', output=None, passive=True).stdout
        host_files_unexpect = set(host_files_actual) - set(host_files_expect)
        if host_files_unexpect:
            raise self.exc_class('unexpected host-specific diff:' + os.linesep + os.linesep.join(sorted(host_files_unexpect)))
        return sorted(host_files_expect)

    @functools.lru_cache(typed=True)
    def master_files(self):
        self.ui.ext_info('Finding master-specific files (common files)...')
        all_files = self.dispatch(git_cmd + 'ls-files', output=None, passive=True).stdout
        master_files = set(all_files) - set(self.host_files())
        return sorted(master_files)

    def adm_config_deliver_master(self):
        'sequence for delivering master & rebasing host branch'
        
        verbosity = 'stderr'
        if self.ui.logger.getEffectiveLevel() == logging.DEBUG:
            verbosity = 'both'

        if self.dispatch(git_cmd + 'diff ' + self.ui.hostname + ' --name-status -- ' + ' '.join(self.host_files()), output=None).stdout:
            raise self.exc_class('commit all host changes before delivering master changes!')

        # stage additions/removals before calling this function
        if self.ui.args.skip < 1:
            self.ui.info('Delivering master-specific changes...')
            self.dispatch(git_cmd + 'stash save', output=verbosity)
            self.dispatch(git_cmd + 'stash branch master', output=verbosity)
            self.ui.debug('Looping interactive commit until error code >0 => no more modifications to stage or we intentionally did not select any')
            try:
                while True:
                    self.dispatch(git_cmd + 'commit --verbose --interactive -uno', output='nopipes')
                    if self.ui.args.dry_run:
                        break
            # continue to clean up in case KeyboardInterrupt occurs
            except BaseException:
                pass

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
        try:
            self.dispatch(git_cmd + 'stash show', output=verbosity)
        except self.exc_class:
            pass
        else:
            self.dispatch(git_cmd + 'stash pop --index', output=verbosity)

    def adm_config_report(self):
        'generate report'
        self.ui.info('Listing host-specific diff:')
        self.dispatch(git_cmd + 'diff ' + self.ui.hostname + ' --name-status -- ' + ' '.join(self.host_files()), output='both')

        self.ui.info('Listing master-specific diff:')
        self.dispatch(git_cmd + 'diff origin/master --name-status -- ' + ' '.join(self.master_files()), output='both')

    def list_repo(self):
        return [''.join(('/',x)) for x in self.dispatch(git_cmd + 'ls-files', output=None).stdout]

    def adm_config_list(self):
        'simple list of managed files for the current host'
        for f in self.list_repo():
            print(f)

    def adm_config_list_branches(self):
        'list managed files of master and host branch'
        self.ui.info('Listing host-specific files:')
        for f in self.host_files():
            print(f)

        self.ui.info('Listing master-specific files:')
        for f in self.master_files():
            print(f)

if __name__ == '__main__':
    app = adm_config(job_class=job.job,
                      ui_class=ui)
    app.run()

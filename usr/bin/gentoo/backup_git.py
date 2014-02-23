# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
#
# General GIT tips'n tricks
# * check heads with git branch
# * git checkout <tag> does not create a branch, in fact it detaches
#   the current HEAD branch
# * git fetch == hg pull
# * show past file without checkin it out: git show v2.5:fs/locks.c
# * creating a file called .gitignore in the top level of your working
#   directory, ie:
#   # Ignore objects and archives.
#   *.[oa]
# * revert to previous file version: git checkout HEAD^ path/to/file
# * cat previous file version: git show HEAD^:path/to/file
# * restore deleted file (rm): git checkout -- *
# * change permissions after restore operations (maybe in export)
#
# ====================================================================
import os
import pylon.base

remove_older_limit      = 1.3
remove_hysteresis_limit = 1.1

class backup_git(pylon.base.base):
    'implement backups using git'

    @pylon.base.memoize
    def initialize_paths(self, src_path, dest_path, opts=''):
        self.ui.ext_info('Initializing git repository at %s...' % dest_path)
        self.dispatch('git '
                      # git repo dir
                      '--git-dir=' + dest_path + ' ' +
                      # working copy
                      '--work-tree=' + src_path + ' ' +

                      # cmd
                      'init',

                      output='stderr')
        return dest_path

    def verify(self, src_path, dest_path, opts=''):
        self.ui.ext_info('Verifying metadata of %s...' % dest_path)
        path = self.initialize_paths(src_path, dest_path)

        # warnings about dangling objects are OK
        self.dispatch('git ' +
                      # git repo dir
                      '--git-dir=' + path + ' ' +
                      # working copy
                      '--work-tree=' + src_path + ' ' +

                      # cmd
                      'fsck --full',

                      output='both')

    def do(self, src_path, dest_path, opts=''):
        self.ui.info('Saving %s to %s...' % (src_path, dest_path))
        path = self.initialize_paths(src_path, dest_path)

        import datetime
        timestamp = datetime.datetime.now().replace(microsecond=0).isoformat()
        tag = datetime.date.today().isoformat()

        # - detect empty directories
        # - touch .gitignore file in them to allow commit
        gitignores = []
        for root, dirs, files in os.walk(src_path, onerror=lambda x: self.ui.error(str(x))):
            if not dirs and not files:
                gitignore = os.path.join(os.path.join(src_path, root), '.gitignore')
                gitignores.append(gitignore)
                self.dispatch('touch ' + gitignore,
                              output='stderr')

        # ensure cleanup of created .gitignore files
        try:

            # - add untracked files
            # - remove disappeared tracked files (due to simple rm)
            # - detect renames, which git status still shows as delete -> add
            self.dispatch('git ' +
                          # git repo dir
                          '--git-dir=' + path + ' ' +
                          # working copy
                          '--work-tree=' + src_path + ' ' +

                          # cmd
                          'add -A',

                          output='stderr')

            try:
                # - commit additions/removals
                # - commit changed files
                self.dispatch('git ' +
                              # git repo dir
                              '--git-dir=' + path + ' ' +
                              # working copy
                              '--work-tree=' + src_path + ' ' +

                              # cmd
                              'commit -am "' + timestamp + '"',

                              output='stderr')
            except self.exc_class:
                # maybe the src is just unchanged?
                changes = self.dispatch('git ' +
                                        # git repo dir
                                        '--git-dir=' + path + ' ' +
                                        # working copy
                                        '--work-tree=' + src_path + ' ' +

                                        # cmd
                                        'diff --summary --cached',

                                        output=None).stdout

                # something's been fishy during commit
                if changes:
                    raise self.exc_class('commit failed')
                else:
                    self.ui.warning('Source %s is unchanged...' % src_path)
            else:

                self.ui.ext_info('Tagging %s...' % dest_path)
                self.dispatch('git ' +
                              # git repo dir
                              '--git-dir=' + path + ' ' +
                              # working copy
                              '--work-tree=' + src_path + ' ' +

                              # cmd
                              # replace already existing tag
                              'tag -f ' + tag + ' HEAD',

                              output='stderr')

                self.ui.ext_info('Compacting diff sets in %s...' % dest_path)
                self.dispatch('git ' +
                              # git repo dir
                              '--git-dir=' + path + ' ' +
                              # working copy
                              '--work-tree=' + src_path + ' ' +

                              # cmd
                              'gc -q ',

                              output='stderr')

                self.verify(src_path, dest_path)

            # check for oversized history
            if self.rec_size(dest_path) / self.rec_size(src_path) > remove_older_limit:
                self.ui.warning('Backup of %s has crossed size limit of %.2f!' % (dest_path,
                                                                                  remove_older_limit))

        finally:
            for f in gitignores:
                if not self.ui.opts.dry_run:
                    os.remove(f)

    def rec_size (self, path):
        return float(self.dispatch('du -s ' + path,
                                   output=None, passive=True).stdout[0].split()[0])

    def info(self, src_path, dest_path, opts=''):
        path = self.initialize_paths(src_path, dest_path)

        # - add untracked files
        # - remove disappeared tracked files (due to simple rm)
        # - detect renames, which git status still shows as delete -> add
        self.dispatch('git ' +
                      # git repo dir
                      '--git-dir=' + path + ' ' +
                      # working copy
                      '--work-tree=' + src_path + ' ' +
         
                      # cmd
                      'add -A ' +
         
                      # add additional options
                      opts,
         
                      output='stderr')
         
        try:
            self.ui.info('Differences %s <-> %s...' % (src_path, dest_path))
            path = self.initialize_paths(src_path, dest_path)
            self.dispatch('git ' +
                          # git repo dir
                          '--git-dir=' + path + ' ' +
                          # working copy
                          '--work-tree=' + src_path + ' ' +
         
                          # cmd
                          'status',
         
                          output='stdout')
        except:
            pass

        self.ui.info('Size ratio of %s: %.2f!' % (dest_path,
                                                  self.rec_size(dest_path) /
                                                  self.rec_size(src_path)))

    def modify(self, src_path, dest_path, opts=''):
        'modify backup destinations according to -o switch'

        cmd = self.ui.opts.options.split(',')[0]
        arg = self.ui.opts.options.split(',')[1]

        if cmd == 'prune':
            self.ui.info('Pruning history of %s...' % dest_path)
            path = self.initialize_paths(src_path, dest_path)

            hashes = self.dispatch('git ' +
                                   # git repo dir
                                   '--git-dir=' + path + ' ' +
                                   # working copy
                                   '--work-tree=' + src_path + ' ' +

                                   # cmd
                                   'log --pretty=oneline',

                                   output=None, passive=True).stdout
            hashes.reverse()

            if self.rec_size(dest_path) / self.rec_size(src_path) > remove_older_limit:


                self.ui.info('Hashes: ' + os.linesep.join(hashes))

                for h in hashes:
                    remove_until_hash = h.split()[0]

                    # this command sequence has been copied from
                    # filter-branch man pages

                    # filter-branch needs to be called in working
                    # directory
                    os.chdir(src_path)

                    self.ui.info('Pruning %s...' % h)
                    self.dispatch('git ' +
                                  # git repo dir
                                  '--git-dir=' + path + ' ' +
                                  # working copy
                                  '--work-tree=' + src_path + ' ' +

                                  # cmd
                                  'filter-branch --parent-filter "sed -e \'s/-p ' + remove_until_hash + '//\'" -- --all ^' + remove_until_hash,

                                  output='stderr')
                    self.ui.debug('Removing backup refs from filter-branch call...')
                    self.dispatch('git ' +
                                  # git repo dir
                                  '--git-dir=' + path + ' ' +
                                  # working copy
                                  '--work-tree=' + src_path + ' ' +

                                  # cmd
                                  'for-each-ref --format="%(refname)" refs/original/ | xargs -n 1 git ' +
                                  # git repo dir
                                  '--git-dir=' + path + ' ' +
                                  # working copy
                                  '--work-tree=' + src_path + ' ' +
                                  'update-ref -d',

                                  output='stderr')
                    self.ui.debug('Invalidate now obsolete objects...')
                    self.dispatch('git ' +
                                  # git repo dir
                                  '--git-dir=' + path + ' ' +
                                  # working copy
                                  '--work-tree=' + src_path + ' ' +

                                  # cmd
                                  'reflog expire --expire=now --all',

                                  output='stderr')
                    self.ui.debug('Forced pruning...')
                    self.dispatch('git ' +
                                  # git repo dir
                                  '--git-dir=' + path + ' ' +
                                  # working copy
                                  '--work-tree=' + src_path + ' ' +

                                  # cmd
                                  'gc -q --prune=now',

                                  output='stderr')

                    if self.rec_size(dest_path) / self.rec_size(src_path) < remove_hysteresis_limit:
                        break

#!/usr/bin/env python
# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
# FIXME
# - shift /opt/portage into hg repo
#   - no extra cruft rule necessary
#   - version control
#   - central repo config files (metadata, ...)
# ====================================================================

# module settings
repo_path = '/mnt/Dropbox/work/projects/workspace/gentoo'
repo_user = 'schweizer'
hosts = [
    'diablo',
    'baal',
    'belial',
    ]

# module imports
import os
import re
import sys

try:
    import gentoo.job
except ImportError:
    # make life easier during initial bootstrap from repo path
    sys.path.append(os.path.join(repo_path, 'usr/bin'))
    import gentoo.job
import gentoo.ui
import pylon.base

class ui(gentoo.ui.ui):

    def cleanup(self):
        super(ui, self).cleanup(self.opts.type)

    def configure(self):
        super(ui, self).configure()
        self.parser.add_option('-t', '--type', type='string',
                               help=self.extract_doc_strings())
        self.parser.add_option('-f', '--force', action='store_true',
                               help='overwrite repository files with system files in all update operations')
        self.parser.add_option('-i', '--force_import', action='store_true',
                               help='overwrite system files with repository files in all update operations')
        self.parser.add_option('-m', '--missing', action='store_true',
                               help='dispatch missing config files (initial linking)')

    def validate(self):
        super(ui, self).validate()
        # this host already configured?
        if self.hostname not in hosts:
            raise self.owner.exc_class('host has not yet been included in list')
        if not self.opts.type:
            raise self.owner.exc_class('specify the type of operation')

class adm_config(pylon.base.base):
    'manage and dispatch config files across multiple hosts'

    def run_core(self):
        getattr(self, self.__class__.__name__ + '_' + self.ui.opts.type)()

    # Config file merging
    # =========================================================================
    @pylon.base.memoize
    def extract_leaf(self, f):
        leaf = ('', f.replace(repo_path, ''))
        for h in hosts:
            if f.find(os.path.join(repo_path, h)) != -1:
                leaf = (h, f.replace(os.path.join(repo_path, h), ''))
        return leaf
    def extract_host_leafs(self):
        'traverse repo and extract config files for current host'

        try:
            os.chdir(repo_path)
        except OSError:
            raise self.exc_class('local repository not found')

        self.ui.ext_info('Extracting file leafs, assuming host ' + self.ui.hostname + '...')

        # extract config files under version control
        files = self.dispatch('su ' + repo_user + ' -c "hg manifest"',
                              passive=True, output=None).stdout
        files.remove('.hgignore')

        # extract config structure
        files = [self.extract_leaf(os.path.abspath(f)) for f in files]

        struct = {'': [],
                  self.ui.hostname: []}

        for (k, v) in files:
            if k in struct:
                struct[k].append(v)

        # remove common files superimposed by host files
        self.superimposed = set(struct['']) & set(struct[self.ui.hostname])
        for x in self.superimposed:
            struct[''].remove(x)
        return struct

    def create_mapping(self, src, dest):
        import shutil

        if (os.path.lexists(dest) and
            not self.ui.opts.force_import):
            self.ui.debug('MAP  ' + dest + ' -> ' + src)
            if not self.ui.opts.dry_run:
                shutil.copy2(dest, src)
        else:
            self.ui.debug('MAP  ' + dest + ' <- ' + src)
            if not self.ui.opts.dry_run:
                # create all necessary parent dirs
                if not os.path.lexists(os.path.dirname(dest)):
                    os.makedirs(os.path.dirname(dest))
                shutil.copy2(src, dest)

    def same_md5(self, f1, f2):
        output = self.dispatch('md5sum %s %s' % (f1, f2),
                                      output=None,
                                      passive=True).stdout
        if output[0].split()[0] == output[1].split()[0]:
            return True
        return False

    def extract_mapping(self, leafs):
        'collect info about file mapping on configured host.'

        struct = {
            'DIFF': [],
            'MISS': [],
            'OK': [],
            }
        for k in leafs:
            for f in leafs[k]:

                # construct repo path
                f_repo = os.path.join(repo_path, k, f.strip('/'))

                # check if file is missing in the system tree
                if os.path.lexists(f) and os.path.isfile(f):

                    # is file only existing in manifest listing of repo?
                    if not os.path.lexists(f_repo):
                        self.ui.warning('REPO_MISS ' + f_repo)

                    # any differences detected?
                    else:
                        if (not self.same_md5(f, f_repo) or
                            # permission difference?
                            os.lstat(f).st_mode != os.lstat(f_repo).st_mode):
                            struct['DIFF'].append((f_repo, f))

                        # file mapping OK!
                        else:
                            struct['OK'].append((f_repo, f))
                            self.ui.ext_info('OK   ' + f + ' <- ' + f_repo)

                else:
                    struct['MISS'].append((f_repo, f))

        return struct

    def check_md5(self, mapping):
        'check for differences to MD5 values stored in portage DB.'

        # generate custom portage object/pkg map once
        import cruft
        objects = {}
        pkg_map = {}
        pkg_err = {}
        for pkg in sorted(cruft.vardb.cpv_all()):
            contents = cruft.vardb._dblink(pkg).getcontents()
            pkg_map.update(dict.fromkeys(contents.keys(), pkg))
            objects.update(contents)

        repo_files = list(pylon.base.flatten(mapping.values()))
        portage_controlled = set(objects.keys()) & set(repo_files)

        # openrc installs user version => MD5 sum always equal (look into ebuild)
        # /etc/conf.d/hostname (sys-apps/openrc-0.11.8)
        for f in portage_controlled:
            (n_passed, n_checked, errs) = cruft.contents_checker._run_checks(cruft.vardb._dblink(pkg_map[f]).getcontents())
            if not filter(lambda e: re.search(f + '.*MD5', e), errs):
                self.ui.warning('=MD5 %s (%s)' % (f, pkg_map[f]))
            else:
                self.ui.debug('!MD5 %s (%s)' % (f, pkg_map[f]))

    def adm_config_report(self, opts=None):
        'generate report'
        leafs = self.extract_host_leafs()
        mapping = self.extract_mapping(leafs)
        self.check_md5(mapping)

        for k in mapping:
            if k != 'OK':
                for m in mapping[k]:
                    self.ui.warning(k + ' ' + m[1] + ' <- ' + m[0])

        # list superimposed files
        for s in self.superimposed:
            self.ui.info('Superimposed file: ' + s)

        self.ui.ext_info('Reporting repository status...')
        self.dispatch('su ' + repo_user + ' -c "hg stat"',
                      output='both')

    def auto_repair(self, mapping):
        'do some automatic repairs'

        # create missing files
        if self.ui.opts.missing:
            for (f_repo, f) in mapping['MISS']:
                self.create_mapping(f_repo, f)
                self.ui.info('repaired MISS ' + f + ' <- ' + f_repo)
            mapping['MISS'] = []
        else:
            for (f_repo, f) in mapping['MISS']:
                self.ui.warning('MISS ' + f + ' <- ' + f_repo)

        # handle unmanaged files
        if self.ui.opts.force:
            for (f_repo, f) in mapping['DIFF']:
                self.create_mapping(f_repo, f)
                self.ui.info('repaired DIFF ' + f + ' <> ' + f_repo)
            mapping['DIFF'] = []

    def adm_config_repair(self, opts=None):
        'repair broken links'
        leafs = self.extract_host_leafs()
        mapping = self.extract_mapping(leafs)
        self.auto_repair(mapping)

        # now try some interactive repairing for the DIFF error class
        for (f_repo, f) in mapping['DIFF']:
            self.ui.info('DIFF ' + f + ' <- ' + f_repo)

            while True:
                key = raw_input('(d)iff, (f)orce, force_(i)mport, (q)uit, (s)kip? ')
                if key == 'q':
                    raise self.exc_class('user break')
                elif key == 's':
                    break
                elif key == 'd':
                    self.dispatch('diff -bdu ' + f + ' ' + f_repo + ' | less',
                                  output='nopipes')
                elif key == 'i':
                    self.ui.opts.force_import = True
                    self.create_mapping(f_repo, f)
                    self.ui.info('REPO ' + f + ' <- ' + f_repo)
                    break
                elif key == 'f':
                    self.ui.opts.force_import = False
                    self.create_mapping(f_repo, f)
                    self.ui.info('SYS  ' + f + ' -> ' + f_repo)
                    break

        # now try some interactive repository admin
        while True:
            mod_files = self.dispatch('su ' + repo_user + ' -c "hg stat -man"',
                                      passive=True, output=None).stdout
            if len(mod_files) == 0:
                break
            mod_list = [str(mod_files.index(f)) + ') ' + f for f in mod_files]
            self.ui.info('give comma-seperated set:' + os.linesep + os.linesep.join(mod_list))
            line = sys.stdin.readline().rstrip(os.linesep)
            files = [mod_files[int(idx)] for idx in line.split(',')]
            files_str = ' '.join(files)

            while True:
                key = raw_input('(d)iff, (c)heck-in, (r)evert, (q)uit, (s)kip? ')
                if key == 'q':
                    raise self.exc_class('user break')
                elif key == 's':
                    break
                elif key == 'd':
                    self.dispatch('su ' + repo_user + ' -c "hg diff ' + files_str + '" | less',
                                  output='nopipes')
                elif key == 'c':
                    msg = sys.stdin.readline()
                    self.dispatch('su ' + repo_user + ' -c \'hg ci -m "' + msg.rstrip(os.linesep) + '" ' + files_str + '\'',
                                  output='stderr')
                    break
                elif key == 'r':
                    self.dispatch('su ' + repo_user + ' -c "hg revert --no-backup ' + files_str + '"',
                                  output='stderr')

                    # automatically restore the mapping after a repo revert
                    self.ui.opts.force = True
                    self.ui.opts.force_import = True
                    self.auto_repair(self.extract_mapping(leafs))

                    break

    def adm_config_bootstrap(self, opts=None):
        'initial config deployment procedure for the host specified by --hostname'

        # auto repair (force importing switch true by default)
        self.ui.opts.force = True
        self.ui.opts.force_import = True
        self.ui.opts.missing = True
        self.auto_repair(self.extract_mapping(self.extract_host_leafs()))

    def adm_config_list(self, opts=None):
        'simple list of managed files for the current host'

        leafs = self.extract_host_leafs()
        for k in leafs.keys():
            for f in leafs[k]:
                if k == '':
                    print f
                else:
                    print k + ': ' + f

if __name__ == '__main__':
    app = adm_config(job_class=gentoo.job.job,
                      ui_class=ui)
    app.run()

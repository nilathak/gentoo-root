#!/usr/bin/env python3
# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
# search filesystem cruft on a gentoo system
#
# inspired by ecatmur's cruft script:
# http://forums.gentoo.org/viewtopic-t-152618-postdays-0-postorder-asc-start-0.html
#
# - ignore syntax
#   ^/path/single_file$
#   ^/path/single_dir/$
#   ^/path/subtree$
# ====================================================================
# TODO
# - check-in on github, write ebuild, write gentoo forum post
# - run pylint over completed python modules -> see if coding
#   standards are met
# - document how ignore patterns can exclude non-portage files AND
#   portage files (for sanity checks)
# - Auto update cache based on parent dir md5
# - implement a switch to check for all non-matching patterns inside a
#   package-specific ignore pattern file (in the cruft.d subtree).
# - .keep files are cruft if .keep_xyz files are provided by package (ie. cron.daily)
# - implement reverse search: determine file in cruft.d, which contains/generates exclusion pattern for specific path. (this will also report redundant exclusions, meaning multiple reverse matching files in cruft.d)
#   maybe just list all ignored cruft files and the corresponding rule/file in parenthesis
# - add switch to exclude symlinks to non-cruft files (until perfect eselect handling is available...)
# - create a usecase for a pattern file with ">=asdf-version" in its name (see multislot useflag with grub)
# ====================================================================

import gentoo.job
import gentoo.ui
import gentoolkit.equery.check
import os
import portage
import pylon.base
import re
import time

cache_base_path = '/tmp'
cache_base_name = 'cruft_cache'
comment_char = '#'
default_ignore_root = '/etc/portage/cruft.d'
contents_checker = gentoolkit.equery.check.VerifyContents()

# assume standard portage tree locatation at /
trees = portage.create_trees()
portdb = trees['/']["porttree"].dbapi
vardb = trees['/']["vartree"].dbapi

# portage vartree dict indices
po_type = 0
po_timestamp = 1
po_digest = 2

# cruft dict indices
co_date = 0

class ui(gentoo.ui.ui):
    def __init__(self, owner):
        super().__init__(owner)
        self.parser_common.add_argument('-i', '--ignore_root',
                                        default=default_ignore_root,
                                        help='give alternative path to directory containing ignore pattern files')
        self.init_op_parser()
        self.parser_report.add_argument('-c', '--check', action='store_true',
                                        help='perform gentoolkit sanity checks on all installed packages (time consuming!)')
        self.parser_report.add_argument('-p', '--path',
                                        default='/',
                                        help='check only specific path for cruft')
        self.parser_report.add_argument('-s', dest='rescan', action='count',
                                        default=0,
                                        help='ignore cache (located in ' + cache_base_path + ') and rescan:\
                                        //-s// rescan system tree,\
                                        //-ss// rescan system & portage tree')
        self.parser_report.add_argument('-f', '--format', choices=('path', 'date', 'rm_chain'),
                                        default='path',
                                        help='\
                                        //date// report cruft objects sorted by modification date,\
                                        //path// report cruft objects sorted by object path (default),\
                                        //rm_chain// report cruft objects as chained rm commands')
        self.parser_report.add_argument('--exclude_valid_softlinks', action='store_true',
                                        help='exclude softlinks which link to non-cruft files')

    def setup(self):
        super().setup()
        if not self.args.op:
            raise self.owner.exc_class('Specify at least one subcommand operation')
        
class cruft(pylon.base.base):
    'search filesystem cruft on a gentoo system, dedicated to all those control freaks out there...'

    def run_core(self):
        import datetime
        t1 = datetime.datetime.now()
        self.data = {}
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()
        self.ui.ext_info(self.ui.args.op + ' took ' + str(datetime.datetime.now() - t1) + ' to complete...')

    def relevant_system_path(self, path):
        # exclude paths which
        # - are masked by ignore patterns
        # - are not even included in the path we'd like to check for cruft
        return not self.data['patterns'].match(path) and path.startswith(self.ui.args.path)
        
    def collect_ignore_patterns(self):
        self.ui.info('Collecting ignore patterns...')

        pattern_files = []
        for root, dirs, files in os.walk(self.ui.args.ignore_root):
            for f in files:
                # assume leaf dirs contain package-specific patterns
                if not dirs:
                    # check if any version of the package is installed
                    pkg = os.path.join(os.path.basename(root), f)
                    if not vardb.match(pkg):
                        self.ui.ext_info('Not installed: ' + pkg)
                        continue
                pattern_files.append(os.path.join(root, f))

        re_list = []

        for pattern_file in pattern_files:
            self.ui.ext_info('Extracting patterns from: ' + pattern_file)
            
            # either we generate regexes from executable scripts, ...
            re_list_raw = []
            if os.access(pattern_file, os.X_OK):
                try:
                    re_list_raw = self.dispatch(pattern_file,
                                                output=None).stdout
                except self.exc_class:
                    self.ui.error('Script failed: ' + pattern_file)

            # ... or we simply read in lines from a text file
            else:
                for line in open(pattern_file, 'r'):

                    # ignore comment lines
                    comment_idx = line.find(comment_char)
                    line_no_comments = line
                    if comment_idx != -1:
                        line_no_comments = line[:comment_idx]

                    re_list_raw.append(line_no_comments)

            # - strip all metachars
            # - interpret spaces as delimiter for multiple patterns
            #   on one line. needed for automatic bash expansion by
            #   {}. however this breaks ignore patterns with spaces!
            re_list_of_file = self.flatten([x.rstrip(os.linesep).strip().split() for x in re_list_raw])

            # pattern sanity checks, to facilitate pattern file debugging
            for regex in re_list_of_file:
                try:
                    re.compile(regex)
                except Exception:
                    self.ui.error('Skipped invalid expression in {1} ({0})'.format(regex,pattern_file))
                else:
                    re_list.append(regex)

        self.ui.debug('Removing dupes & empty strings...')
        re_set = set(re_list) - set([''])

        self.ui.debug('Compiling all expressions into one long regex...')
        return re.compile('|'.join(re_set))
        
    def collect_portage_objects(self):
        if 'patterns' not in self.data: self.data['patterns'] = self.collect_ignore_patterns()

        self.ui.info('Collecting objects managed by portage...')
        objects = {}
        if self.ui.args.check:
            self.ui.info('Checking package sanity using gentoolkit...')
        for pkg in sorted(vardb.cpv_all()):
            contents = vardb._dblink(pkg).getcontents()
            if self.ui.args.check:

                # iterate one contents item at a time to allow easy mapping of error <-> object path
                for k, v in contents.items():
                    if self.relevant_system_path(k):
                        (n_passed, n_checked, errs) = contents_checker._run_checks({k:v})
                        for e in errs:
                            self.ui.error(pkg + ': ' + e)

            objects.update(contents)

        self.ui.debug('Add trailing slashes to directories for easier regex matching...')
        objects_slashes = {}
        for k, v in objects.items():
            if objects[k][po_type] == 'dir':
                objects_slashes[k + '/'] = v
            else:
                objects_slashes[k] = v
        objects = objects_slashes

        self.ui.debug('Flattening out portage paths containing symlinks...')
        # just flatten out the dirname part to avoid tinkering with symlinks introduced by portage itself
        objects_symlinks = {}
        for k, v in objects.items():
            objects_symlinks[os.path.join(os.path.realpath(os.path.dirname(k)),
                                          os.path.basename(k))] = v
        objects = objects_symlinks
        
        return objects

    def collect_system_objects(self):
        if 'patterns' not in self.data: self.data['patterns'] = self.collect_ignore_patterns()

        self.ui.info('Collecting objects in system tree...')
        import copy
        objects = []
        for root, dirs, files in os.walk(self.ui.args.path, onerror=lambda x: self.ui.error(str(x))):

            for d in copy.copy(dirs):
                path = os.path.join(root, d)

                # symlinks to dirs are regarded as dir for os.walk. since it's difficult to determine the
                # target of a 'sym' portage kind -> fix this here...
                if os.path.islink(path):
                    dirs.remove(d)
                    files.append(d)
                    continue

                # remove excluded subtrees early to speed up walk (eg, user data)
                # leave dir without slash in objects => filtered by this regex anyway
                if self.data['patterns'].match(path):
                    dirs.remove(d)
                    objects.append(path)
                    continue

                # add a trailing slash to allow easy distinction between subtree and single dir exclusion
                objects.append(path + '/')
            
            for f in files:
                path = os.path.join(root, f)
                objects.append(path)
                
                # report broken symlinks but keep them in list (needed for portage - system report)
                if not os.path.exists(path):
                    self.ui.error('Broken symlink detected: ' + path)

        return objects

    def collect_cruft_objects(self):
        if 'patterns' not in self.data: self.data['patterns'] = self.collect_ignore_patterns()
        if 'portage' not in self.data: self.data['portage'] = self.collect_portage_objects()
        if 'system' not in self.data: self.data['system'] = self.collect_system_objects()

        self.ui.info('Identifying system/portage mismatch...')
        self.ui.debug('Generating difference set (portage - system)...')
        mismatch = list(set(self.data['portage'].keys()) - set(self.data['system']))
        for path in sorted(mismatch):
            if not os.path.exists(path) and self.relevant_system_path(path):
                self.ui.error('Portage object missing on system: ' + path)

        self.ui.info('Identifying cruft...')
        self.ui.debug('Generating difference set (system - portage)...')
        cruft = list(set(self.data['system']) - set(self.data['portage'].keys()))

        self.ui.debug('Applying ignore patterns...')
        remaining = [path for path in cruft if not self.data['patterns'].match(path)]

        self.ui.debug('Removing parent directories of cruft files...')
        ignored = list(set(cruft) - set(remaining))
        for path in ignored:
            remaining = list(filter(lambda x: not path.startswith(x), remaining))
        
        self.n_ignored = len(cruft) - len(remaining)

        # add a date info to the remaining objects
        cruft = {}
        for path in sorted(remaining):
            # remove cruft -> non-cruft symlinks
            if (not os.path.islink(path) or
                os.path.realpath(path) in remaining or
                not self.ui.args.exclude_valid_softlinks):
                try:
                    cruft[path] = [time.localtime(os.lstat(path).st_mtime)]
                except OSError:
                    self.ui.error('Path disappeared: ' + path)

        return cruft

    def collect_data(self):
        self.ui.debug('Collecting data and using cache when possible...')
        self.data['patterns'] = self.collect_ignore_patterns()

        import pickle
        cache = {}
        cache_path = os.path.join(cache_base_path, cache_base_name + '_' + self.ui.hostname)
        if (os.access(cache_path, os.R_OK) and
            self.ui.args.rescan <= 1):
            with open(cache_path, 'rb') as cache_file:
                self.ui.info('Loading cache...')
                cache = pickle.load(cache_file)

        dirty = False
        if ('system' not in cache or
            self.ui.args.rescan > 0):
            self.data['system'] = cache['system'] = self.collect_system_objects()
            dirty = True
        else:
            self.ui.warning('Restoring system object list from cache...')
            self.data['system'] = cache['system']
        if ('portage' not in cache or
            self.ui.args.rescan > 1 or
            self.ui.args.check):
            self.data['portage'] = cache['portage'] = self.collect_portage_objects()
            dirty = True
        else:
            self.ui.warning('Restoring portage object list from cache...')
            self.data['portage'] = cache['portage']
        if dirty:
            with open(cache_path, 'wb') as cache_file:
                self.ui.info('Storing cache...')
                pickle.dump(cache, cache_file, -1)

    def cruft_report(self):
        'identify potential cruft objects on your system'
        self.collect_data()
        self.cruft_dict = self.collect_cruft_objects()

        if self.cruft_dict:
            cruft_keys = list(self.cruft_dict.keys())

            # useful sort keys
            path = lambda x: x
            date = lambda x: self.cruft_dict[x][co_date]
            path_str = lambda x: path(x)
            date_str = lambda x: time.asctime(date(x))

            # sort & format according to option
            fmt = '{path_str}, {date_str}'
            reverse = False
            sort_key = path
            if self.ui.args.format == 'date':
                reverse = True
                sort_key = date
            if self.ui.args.format == 'rm_chain':
                fmt = 'rm -rf "{path_str}" && \\'
            cruft_keys.sort(key=sort_key, reverse=reverse)

            self.ui.info('Cruft objects:' + os.linesep +
                         os.linesep.join(
                             [fmt.format(path_str=path_str(co),
                                         date_str=date_str(co))
                              for co in cruft_keys]))
            self.ui.warning('Cruft objects identified: {0}'.format(len(cruft_keys)))

        self.ui.info('Cruft files ignored: {0}'.format(self.n_ignored))

    def cruft_list_patterns(self):
        'list collected ignore patterns'
        self.ui.info('Ignore patterns:' + os.linesep +
                     os.linesep.join(sorted(self.collect_ignore_patterns().pattern.split('|'))))

if __name__ == '__main__':
    app = cruft(job_class=gentoo.job.job,
                ui_class=ui)
    app.run()
    # import cProfile
    # try:
    #    cProfile.run('app.run()', '/tmp/fooprof')
    # except:
    #    pass
    # import pstats
    # p = pstats.Stats('/tmp/fooprof')
    # p.sort_stats('cumulative').print_stats(30)
    # p.sort_stats('time').print_stats(30)




#    # helpers
#    # =================================================
#    def users():
#        return self.dispatch('awk -F: \'{ printf $1 "," }\' /etc/passwd',
#                             output=None).stdout[0].rstrip(',').split(',')
#    def interfaces():
#        return self.dispatch('for service in /etc/init.d/net.*; do echo -n ${service##*.},; done',
#                             output=None).stdout[0].rstrip(',').split(',')
#    def grab_setting(v):
#        return self.dispatch('[[ -r /etc/make.globals ]] && source /etc/make.globals; \
#        [[ -r ${PORTDIR-/usr/portage}/profiles/base/make.defaults ]] && source ${PORTDIR-/usr/portage}/profiles/base/make.defaults; \
#        [[ -r /etc/make.profile/make.defaults ]] && source /etc/make.profile/make.defaults; \
#        [[ -r /etc/make.conf ]] && source /etc/make.conf; \
#        echo ${%s}' % v, output=None).stdout
# 
#    # conditions
#    # =================================================
#    def installed(v, slot=None, use=None):
#        import portage
#        return len(portage.db['/']['vartree'].dbapi.match(v)) > 0
# 
#    # Things belonging to users
#    ignore(''.join(['^/var/spool/cron/crontabs/' + u for u in users()]))
#    ignore(''.join(['^/var/spool/mail/' + u for u in users()]))
# 
#    # Local files
#    ignore('''
#    ^/etc/init\.d/local
#    ^/usr/local
#    ^/usr/share/applications/local
#    ^/usr/share/control-center-2\.0/capplets/local
#    ^/usr/share/faces
#    ^/usr/share/fonts/local
#    ^/usr/share/pixmaps/local
#    ^/usr/share/sounds/local
#    ''')
# 
#    # Admin-managed data and resources (files needed to regenerate a system)
#    ignore('''
#        ^/etc/cron\.(hourly|daily|weekly|monthly|allow|deny)
#        ^/etc/dnsdomainname
#        ^/etc/fstab
#        ^/etc/group
#        ^/etc/group-
#        ^/etc/gshadow
#        ^/etc/gshadow-
#        ^/etc/hostname
#        ^/etc/hosts(\.(allow|deny|equiv))*
#        ^/etc/issue(\.net)*
#        ^/etc/make\.(conf|profile)
#        ^/etc/localtime
#        ^/etc/motd
#        ^/etc/passwd
#        ^/etc/passwd-
#        ^/etc/portage
#        ^/etc/runlevels
#        ^/etc/shadow
#        ^/etc/shadow-
#        ^/etc/skel
#        ^/etc/xprofile
#        ^/var/lib/portage
#    ''')
# 
#    # Kernel and console
#    ignore('''
#    ^/etc/mtab
#    ^/var/log/dmesg
#    ^/var/run/console/console\.lock
#    ''')
#    ignore(''.join(['^/var/run/console/' + u for u in users()]))
# 
# 
#    # java dependant ignores
#    if java_installed():
#        ignore('^/etc/\.java')
#    if (java_installed('sun') or
#        java_installed('ibm-jdk')):
#        ignore(''.join(nsplugin('libjavaplugin_oji.so')))
# 
#    # ignores depending on certain files
#    if exists('/var/lib/init.d/started/clock'):
#        ignore('^/etc/adjtime')
#    if exists('/var/lib/init.d/started/hostname'):
#        ignore('^/etc/env\.d/01hostname')
#    if (exists('/var/lib/init.d/started/bootmisc') or
#        exists('/var/lib/init.d/started/domainname')):
#        ignore('^/etc/resolv\.conf')
#    if exists('/var/lib/init.d/started/urandom'):
#        ignore('^/var/run/random-seed')
# 
#    # ignores depending on eclasses
#    if eclass('games'):
#        ignore('^/etc/env\.d/90games')
#    if eclass('linux-mod'):
#        ignore('^/usr/share/module-rebuild')
# 
#    # Package dependant ignores
#    if installed('x11-base/xorg-x11'):
#        ignore('/\.fonts\.cache-1')
#    if installed('sys-apps/hal'):
#        ignore('^/etc/\.fstab\.hal\.(1|i)')
#    if installed('sys-apps/shadow'):
#        ignore('^/etc/\.pwd\.lock')
#    if installed('media-libs/alsa-lib'):
#        ignore('^/etc/asound\.state')
#    if installed('app-shells/bash'):
#        ignore('^/etc/bash/bash_logout')
#    if installed('sys-fs/e2fsprogs'):
#        ignore('^/etc/blkid\.tab(\.old)*')
#    if installed('net-mail/courier-imap'):
#        ignore('^/etc/courier-imap/(authdaemond\.conf|imapd\.pem)')
#    if installed('net-dns/ddclient'):
#        ignore('^/etc/ddclient/ddclient\.(cache|conf)')
#    if installed('app-misc/fdutils'):
#        ignore('^/etc/driveprm')
#    if installed('dev-java/java-config'):
#        ignore('^/etc/env\.d/20java')
#    if installed('net-fs/nfs-utils'):
#        ignore('^/etc/exports')
#    if installed('net-print/foomatic'):
#        ignore('^/etc/foomatic\.cups')
#    if installed('app-portage/gentoolkit-dev'):
#        ignore('^/etc/gensync/.*\.syncsource')
#    if installed('x11-libs/gtk+'):
#        ignore('''
#        ^/etc/gtk-2\.0/gtk\.immodules
#        ^/etc/gtk-2\.0/gdk-pixbuf\.loaders
#        ''')
#    if installed('media-libs/libgphoto2'):
#        ignore('^/etc/hotplug/usb/usbcam-gphoto2\.usermap')
#    if installed('sys-apps/sysvinit'):
#        ignore('^/etc/ioctl\.save')
#    if installed('net-nds/openldap'):
#        ignore('^/etc/openldap/ssl/ldap\.pem')
#    if installed('dev-util/pretrace'):
#        ignore('^/etc/pretrace\.conf')
#    if installed('net-ftp/proftpd'):
#        ignore('^/etc/proftpd/proftpd\.conf')
#    if installed('x11-libs/pango'):
#        ignore('^/etc/pango/pango\.modules')
#    if installed('net-misc/rsync'):
#        ignore('^/etc/rsync/rsyncd\.conf')
#    if installed('app-text/docbook-dsssl-stylesheets'):
#        ignore('^/etc/sgml/dsssl-docbook-stylesheets\.cat')
#    if installed('app-text/docbook-xml-dtd'):
#        ignore('^/etc/xml/docbook')
#    if installed('sys-devel/gcc-config'):
#        ignore('^/(usr/bin/(gcc-config|(${CHOST}-)*(gcc|cpp|cc|c\+\+|g\+\+|f77|g77|gcj)(32|64)*)|lib/(cpp|libgcc_s\.so(\.1)*))')
#    if installed('media-gfx/gimp'):
#        ignore('^/usr/bin/gimp')
#    if installed('dev-lang/python'):
#        ignore('''
#        ^/usr/bin/python(2)*
#        ^/var/log/python-updater\.log
#        ''')
#    if installed('net-www/netscape-flash'):
#        ignore(''.join(nsplugin('libflashplayer.so')))
#        ignore(''.join(nsplugin('flashplayer.xpt')))
#    if installed('media-video/vlc'):
#        ignore(''.join(nsplugin('libvlcplugin.so')))
#    if installed('=dev-lang/python-2.3*'): VERSIONS !!!!!!!!!!!!!
#        ignore('^/usr/lib/python2\.3/lib-dynload/bz2\.so')
#    if installed('dev-python/pygtk'):
#        ignore('^/usr/lib/python${PYVER}/site-packages/pygtk\.(py|pyc|pyo|pth)')
#    if installed('media-libs/fontconfig'):
#        ignore('^/usr/share/fonts/afms')
#    if installed('media-fonts/urw-fonts'):
#        ignore(''.join(fontdir('urw-fonts')))
#    if installed('sys-apps/man'):
#        ignore('^/usr/share/man/whatis')
#    if installed('media-gfx/gimp'):
#        ignore('^/usr/share/pixmaps/wilber-icon\.png')
#    if installed('app-admin/gnome-system-tools'):
#        ignore('^/var/cache/setup-tool-backends')
#    if installed('net-firewall/iptables'):
#        ignore('^/var/lib/iptables/rules-save')
#    if installed('net-fs/nfs-utils'):
#        ignore('^/var/lib/nfs/(e|rm|x)tab')
#    if installed('sys-apps/dbus'):
#        ignore('^/var/lib/dbus/(pid|system_bus_socket)')
#    if installed('sys-apps/slocate'):
#        ignore('^/var/lib/slocate/slocate\.db')
#    if installed('sys-apps/pam-login'):
#        ignore('^/var/log/lastlog')
#    if installed('app-admin/sudo'):
#        ignore('^/var/run/sudo')
#    if installed('sys-process/cronbase'):
#        ignore('^/var/spool/cron/lastrun/(cron\.(hourly|daily|weekly|monthly)|lock)')
#    if (installed('net-misc/ssh') or
#        installed('net-misc/openssh')):
#        ignore('^/etc/ssh/ssh_host_(dsa_|rsa_)*key(\.pub)*')
#    if installed('app-emulation/vmware-workstation'):
#        ignore('''
#        ^/etc/vmware
#        ^/var/lock/subsys/vmware
#        ^/var/run/vmware
#        ''')
#    if installed('app-text/docbook-sgml-dtd'):
#        ignore('''
#        ^/etc/sgml/sgml\.cenv
#        ^/etc/sgml/sgml\.env
#        ''')
#        # cat /var/db/pkg/app-text/docbook-sgml-dtd-*/SLOT | sed 's:^:/etc/sgml/sgml-docbook-:; s:$:.cat:'
#    if installed('app-text/sgml-common'):
#        ignore('''
#        ^/etc/sgml/sgml-ent\.cat
#        ^/etc/sgml/sgml-docbook\.cat
#        ^/etc/sgml/catalog
#        ''')
#    if installed('app-text/sgmltools-lite'):
#        ignore('''
#        ^/etc/env\.d/93sgmltools-lite
#        ^/etc/sgml/sgml-lite\.cat
#        ''')
#    if installed('dev-db/mysql'):
#        ignore('''
#        ^/var/lib/mysql
#        ^/var/log/mysql
#        ^/var/run/mysqld
#        ''')
#    if installed('dev-lang/perl'):
#        ignore('''
#        ^/usr/lib/libperl\.so
#        ''')
#        # perl -e 'use Config; print $Config{installsitearch};'
#        # perl -e 'use Config; print $Config{privlib}."/CPAN/Config.pm";'
#        # perl -e 'use Config; print $Config{installarchlib}."/perllocal.pod";'
#    if installed('dev-ruby/ruby-config'):
#        ignore('''
#        ^/usr/lib/libruby\.so
#        ^/usr/share/man/man1/ruby\.1\.gz
#        ^/usr/bin/(ruby|irb|erb|testrb|rdoc)
#        ''')
#    if installed('dev-util/ccache'):
#        ignore('''
#        ^/usr/lib/ccache/bin/(c\+\+|cc|g\+\+|gcc|${CHOST}-(c\+\+|g\+\+|gcc))
#        ^${CCACHE_DIR}
#        ''')
#    if installed('kde-base/kdebase'):
#        ignore('''
#        ^/usr/kde/3\.2/share/templates/\.source/emptydir
#        ^/var/log/kdm\.log
#        ^/var/run/kdm\.pid
#        ''')
#        ignore(''.join(['^/var/tmp/kdecache-' + u for u in users()]))
#    if installed('mail-mta/postfix'):
#        ignore('''
#        ^/etc/mail/aliases\.db
#        ^/var/spool/postfix
#        ''')
#    if installed('media-gfx/xloadimage'):
#        ignore('''
#        ^/usr/bin/xview
#        ^/usr/bin/xsetbg
#        ^/usr/share/man/man1/xview\.1\.gz
#        ^/usr/share/man/man1/xsetbg\.1\.gz
#        ''')
#    if installed('net-fs/nfs-utils'):
#        ignore('''
#        ^/var/run/rpc\.statd\.pid
#        ^/var/lib/nfs/(sm|sm\.bak|state)
#        ''')
#    if installed('net-fs/samba'):
#        ignore('''
#        ^/etc/samba/smb\.conf
#        ^/etc/samba/private
#        ^/var/spool/samba
#        ^/var/log/samba
#        ^/var/log/samba3
#        ^/var/run/samba
#        ^/var/cache/samba
#        ^/var/lib/samba
#        ''')
#    if installed('net-misc/dhcpcd'):
#        ignore('''
#        ^/etc/ntp\.conf
#        ^/etc/ntp\.conf.sv
#        ^/etc/resolv\.conf
#        ^/etc/resolv\.conf\.sv
#        ^/etc/yp\.conf
#        ^/etc/yp\.conf\.sv
#        ''')
#        ignore(''.join(['^/var/cache/dhcpcd-%s\.cache' % i for i in interfaces()]))
#        ignore(''.join(['^/var/lib/dhcpc(/dhcpcd(\.exe|-%s\.info(\.old)*))*' % i for i in interfaces()]))
#        ignore(''.join(['^/var/run/dhcpcd-%s\.pid' % i for i in interfaces()]))
#    if installed('net-misc/ntp'):
#        ignore('''
#        ^/etc/ntp\.conf
#        ^/var/log/ntp\.log
#        ''')
#    if installed('net-misc/nxserver-freenx'):
#        ignore('''
#        ^/etc/env\.d/50nxserver
#        ^/usr/NX/home/nx/\.ssh/known_hosts
#        ^/usr/NX/var/db/closed
#        ^/usr/NX/var/db/failed
#        ^/usr/NX/var/db/running
#        ''')
#    if installed('net-misc/openssh'):
#        ignore('^/etc/ssh/(moduli|ssh_config|sshd_config)')
#    if installed('net-print/cups'):
#        ignore('''
#        ^/etc/cups
#        ^/etc/printcap
#        ^/var/log/cups
#        ^/var/spool/cups
#        ''')
#    if installed('=net-www/apache-2*'):
#        ignore('''
#        ^/var/lib/dav
#        ^/var/log/apache2
#        ^/var/cache/apache2
#        ^/etc/apache2/(conf/(ssl|vhosts)|(extra)*modules|lib|logs)
#        ''')
#    if installed('sys-apps/acpid'):
#        ignore('''
#        ^/var/log/acpid
#        ^/var/run/acpid\.socket
#        ''')
#    if installed('sys-apps/baselayout'):
#        ignore('''
#        ^/etc/env\.d/02locale
#        ^/etc/gentoo-release
#        ^/etc/modprobe\.conf
#        ^/etc/modprobe\.conf\.old
#        ^/etc/modprobe\.devfs
#        ^/etc/modprobe\.devfs\.old
#        ^/etc/modules\.conf
#        ^/etc/modules\.conf\.old
#        ^/etc/ld\.so\.conf
#        ^/etc/prelink\.conf
#        ^/etc/profile\.env
#        ^/etc/csh\.env
#        ^/usr/share/man/\.keep\.gz
#        ^/var/lib/init\.d
#        ''')
#    if installed('sys-apps/portage'):
#        ignore('''
#        ^${PORTDIR}
#        ^/var/cache/edb
#        ^/var/db/pkg
#        ^/var/log/emerge\.log
#        ^/var/log/emerge_fix-db\.log
#        ^${PORT_LOGDIR}
#        ^${PORTAGE_TMPDIR}/portage
#        ''')

#######################################################################################
#######################################################################################
#######################################################################################

# function grab_setting() {
#    name="$1"; default="$2"
#    [[ "${!name+ISSET}" ]] && return
#    export $name="$(
#    eval $name="$default"
#    [[ -r /etc/make.globals ]] \
#    && source /etc/make.globals
#    [[ -r ${PORTDIR-/usr/portage}/profiles/base/make.defaults ]] \
#    && source ${PORTDIR-/usr/portage}/profiles/base/make.defaults
#    [[ -r /etc/make.profile/make.defaults ]] \
#    && source /etc/make.profile/make.defaults
#    [[ -r /etc/make.conf ]] \
#    && source /etc/make.conf
#    echo "${!name}"
#    )"
# }
#
# grab_setting DISTDIR ${PORTDIR}/distfiles
# grab_setting PKGDIR ${PORTDIR}/packages
# grab_setting PORT_LOGDIR /var/log/portage
# grab_setting PORTAGE_TMPDIR /var/tmp
# grab_setting CCACHE_DIR $PORTAGE_TMPDIR/ccache
#
# grab_setting CHOST i386-pc-linux-gnu
# grab_setting ARCH x86
# grab_setting ACCEPT_KEYWORDS $ARCH
#
#
# # comma-separated e.g. eth0,eth1,ppp0,lo
# interfaces=$(for service in /etc/init.d/net.*; do echo -n ${service##*.},; done)
# interfaces=${interfaces%,}
# users=$(awk -F: '{ printf $1 "," }' /etc/passwd)
# users=${users%,}
#
# PYVER_ALL="$(python -V 2>&1 | cut -d' ' -f2)"
# PYVER_MAJOR=$(echo ${PYVER_ALL} | cut -d. -f1)
# PYVER_MINOR=$(echo ${PYVER_ALL} | cut -d. -f2)
# PYVER_MICRO=$(echo ${PYVER_ALL} | cut -d. -f3-)
# PYVER="${PYVER_MAJOR}.${PYVER_MINOR}"
#
# NS_PLUGINS_DIR="$(sed 's/^PLUGINS_DIR="\(.*\)"$/\1/;t;d' $PORTDIR/eclass/nsplugins.eclass)"
# function nsplugin() {
#    for MYFILE in "${@}"; do
#    	echo "/usr/lib/${NS_PLUGINS_DIR}/${MYFILE}"
#    done
# }
#
# function fontdir() {
#    for MYDIR in "${@}"; do
#    	if [[ "$MYDIR" == /* ]]; then
#    		echo ${MYDIR}/{fonts.{cache-1,dir,list,scale},encodings.dir,Fontmap}
#    	elif [[ "${MYDIR}" ]]; then
#    		fontdir /usr/share/fonts/${MYDIR}
#    		fontdir ${MYDIR%/*}
#    	else
#    		fontdir /usr/share/fonts
#    	fi
#    done
# }
#
# function motif-profile() {
#    echo /usr/lib/motif/$1
#    echo /etc/env.d/15$1
#    echo /usr/share/man/man5/Traits.5.gz
# }
#
# JAVA_PROVIDES="dev-java/blackdown-jdk dev-java/blackdown-jre dev-java/ibm-jdk dev-java/ibm-jre dev-java/kaffe dev-java/sun-j2ee dev-java/sun-j2sdk dev-java/sun-jdk dev-java/sun-jre"
# function has_java() {
#    for java in $JAVA_PROVIDES; do
#    	[[ "$java" == *"$1"* ]] && has_version $java && return 0
#    done
#    return 1
# }
#
# function has_eclass() {
#    grep -q '\<'"$1"'\>' /var/db/pkg/*/*/INHERITED
# }
#
# function has_initscript() {
#    [[ -h "/var/lib/init.d/started/$1" ]]
# }
#
# function XPIApp() {
#    [[ "$#" -gt 1 ]] && for x in "${@:2}"; do XPIApp "$x"; done
#    path="$1"
#    [[ "$path" != /* ]] && path="/usr/lib/$path"
#    [[ "$path" == */ ]] && path="${path%/}"
#    echo "$path"/{chrome/{chrome.rdf,overlayinfo},components/{compreg.dat,xpti.dat},components.ini,chrome,extensions,install.log,searchplugins}
# }
#
# function expandpathsea() {
#    # see mktexlsr(1), <http://tug.org/teTeX>
#    [[ "$#" -gt 1 ]] && for x in "${@:2}"; do expandpathsea "$x"; done
#    lsR="$1"; path="${lsR%/*}"
#    [[ -r "$lsR" ]] || return
#    sed -e '/^$/d;/^%/d;/:$/{s/:$//;h;d};G;s:\(.*\)\n\(.*\):\2/\1:;s://:/:;s:^./::;/^$/d;ta;:a;p;s:/[^/]*$::;ta;d' "$lsR" \
#    | sort -u | sed -e 's:^:'"${path//:/\:}"'/:'
# }
#
#
# # Things belonging to users
# PRUNE_USERS="
#    $(eval echo /var/spool/cron/crontabs/{${users}})
#    $(eval echo /var/spool/mail/{${users}})
# "
#
# # Local files
# PRUNE_LOCAL="
#    /etc/init.d/local
#    /usr/local
#    /usr/share/applications/local
#    /usr/share/control-center-2.0/capplets/local
#    /usr/share/faces
#    /usr/share/fonts/local
#    /usr/share/pixmaps/local
#    /usr/share/sounds/local
# "
#
# # Admin-managed data and resources (files needed to regenerate a system)
# PRUNE_ADMIN="
#    $(echo /etc/cron.{hourly,daily,weekly,monthly,allow,deny})
#    /etc/dnsdomainname
#    /etc/hostname
#    $(echo /etc/hosts{,.{allow,deny,equiv}})
#    $(echo /etc/issue{,.net})
#    $(echo /etc/make.{conf,profile})
#    /etc/motd
#    /etc/portage
#    /etc/skel
#    /var/lib/portage
# "
#
# # Kernel and console
# PRUNE_NOOWNER="
#    /var/log/dmesg
#
#    $(eval echo /var/run/console/{${users}})
#    /var/run/console/console.lock
# "
#
# PRUNE="${PRUNE}
# $(has_version x11-base/xorg-x11		&& echo "/.fonts.cache-1")
# $(has_version sys-apps/hal			&& echo /etc/.fstab.hal.{1,i})
# $(has_java			        		&& echo "/etc/.java")
# $(has_version sys-apps/shadow		&& echo "/etc/.pwd.lock")
# $(has_initscript clock				&& echo "/etc/adjtime")
# $(has_version app-shells/bash		&& echo "/etc/bash/bash_logout")
# $(has_version sys-fs/e2fsprogs		&& echo /etc/blkid.tab{,.old})
# $(has_version net-mail/courier-imap	&& echo /etc/courier-imap/{authdaemond.conf,imapd.pem})
# $(has_initscript hostname			&& echo "/etc/env.d/01hostname")
# $(has_version dev-java/java-config  && echo "/etc/env.d/20java")
# $(has_eclass games 					&& echo "/etc/env.d/90games")
# $(has_version net-fs/nfs-utils		&& echo "/etc/exports")
# $(has_version net-print/foomatic    && echo "/etc/foomatic.cups")
# $(has_version x11-libs/gtk+         && echo "/etc/gtk-2.0/gtk.immodules
#    				/etc/gtk-2.0/gdk-pixbuf.loaders")
# $(has_version media-libs/libgphoto2	&& echo "/etc/hotplug/usb/usbcam-gphoto2.usermap")
# $(has_version sys-apps/sysvinit		&& echo "/etc/ioctl.save")
# $(has_version x11-libs/pango        	&& echo "/etc/pango/pango.modules")
# $(has_version sys-devel/prelink     	&& echo "/etc/prelink.cache")
# $(has_initscript bootmisc \
# || has_initscript domainname 		&& echo "/etc/resolv.conf")
# $(has_version net-misc/rsync		&& echo "/etc/rsync/rsyncd.conf")
# $(has_version app-text/docbook-dsssl-stylesheets && echo "/etc/sgml/dsssl-docbook-stylesheets.cat")
# $(has_version app-text/docbook-xml-simple-dtd && echo "/etc/sgml/xml-simple-docbook-$(cpvr_to_v $(best_version app-text/docbook-xml-simple-dtd)).cat")
# $(has_version media-gfx/splashutils	&& echo "/etc/splash/default")
# $(has_version x11-misc/xscreensaver 	&& echo "/etc/X11/app-defaults/XScreenSaver")
# $(has_version xfce-base/xfce4-base	&& echo "/etc/X11/Sessions/xfce4")
# $(has_version gnome-base/libglade   	&& echo "/etc/xml/catalog")
# $(has_version app-text/docbook-xml-dtd 	&& echo "/etc/xml/docbook")
#
# $(has_version sys-devel/gcc-config	&& echo /{usr/bin/{gcc-config,{,${CHOST}-}{gcc,cpp,cc,c++,g++,f77,g77,gcj}{,32,64}},lib/{cpp,libgcc_s.so{,.1}}})
# $(has_version dev-lang/python 		&& echo /usr/bin/python{,2})
#
# $(has_version www-client/mozilla-bin \
# || has_version www-client/mozilla 	&& XPIApp "mozilla")
# $(has_version www-client/mozilla-firefox-bin \
# || has_version www-client/mozilla-firefox && XPIApp "MozillaFirefox")
# $(has_version mail-client/mozilla-thunderbird-bin \
# || has_version mail-client/mozilla-thunderbird && XPIApp "MozillaThunderbird")
#
# $(has_version app-text/djvu 		&& nsplugin "nsdejavu.so")
# $(has_version net-www/netscape-plugger 	&& nsplugin "plugger.so")
# $(has_version net-www/mplayerplug-in 	&& nsplugin "mplayerplug-in.so")
# $(has_version net-www/netscape-flash 	&& nsplugin "libflashplayer.so" "flashplayer.xpt")
# $(has_version gnome-base/librsvg	&& nsplugin libmozsvgdec.{a,la,so})
# $(has_version net-www/gplflash 		&& nsplugin "npflash.so")
# $(has_java blackdown   			&& nsplugin "javaplugin_oji.so")
# $(has_java sun || has_java ibm-jdk	&& nsplugin "libjavaplugin_oji.so")
# $(has_java ibm-jre 			&& nsplugin "javaplugin.so")
# $(has_version media-video/vlc 		&& nsplugin "libvlcplugin.so")
# $(has_version '=x11-libs/openmotif-2.2*' && motif-profile openmotif-2.2)
# $(has_version '=dev-lang/python-2.3*' 	&& echo "/usr/lib/python2.3/lib-dynload/bz2.so")
# $(has_version dev-python/pygtk 		&& echo /usr/lib/python${PYVER}/site-packages/pygtk.{py,pyc,pyo,pth})
# $(has_version media-libs/fontconfig	&& echo "/usr/share/fonts/afms")
# $(has_version media-fonts/urw-fonts 	&& fontdir urw-fonts)
# $(has_version sys-apps/man 		&& echo "/usr/share/man/whatis")
# $(has_eclass linux-mod			&& echo "/usr/share/module-rebuild")
# $(has_version net-firewall/iptables		&& echo "/var/lib/iptables/rules-save")
# $(has_version net-fs/nfs-utils			&& echo /var/lib/nfs/{e,rm,x}tab)
# $(has_version app-text/scrollkeeper		&& echo "/var/lib/scrollkeeper")
# $(has_version sys-apps/dbus  			&& echo /var/lib/dbus/{pid,system_bus_socket})
# $(has_version sys-apps/slocate			&& echo "/var/lib/slocate/slocate.db")
# $(has_version sys-apps/pam-login		&& echo "/var/log/lastlog")
# $(has_version sys-apps/partimage	&& echo /var/log/partimage-debug.log{,_latest})
# $(has_version mail-mta/qmail		&& echo "/var/qmail")
# $(has_version app-admin/sysklogd	&& echo /var/run/{sys,k}logd.pid)
# $(has_version net-misc/netapplet    	&& echo "/var/run/netapplet.socket")
# $(has_initscript urandom		&& echo "/var/run/random-seed")
# $(has_version app-misc/screen		&& eval echo /var/run/screen/S-{${users}})
# $(has_version app-admin/sudo        	&& echo "/var/run/sudo")

# # Packages which drop files or directories on more than one place go here,
# # listed alphabetically by category/package.
# has_version app-emulation/vmware-workstation \
#    && PRUNE="${PRUNE}
#    /etc/vmware
#    /var/lock/subsys/vmware
#    /var/run/vmware"
# has_version app-text/docbook-sgml-dtd \
#    && PRUNE="${PRUNE}
#    $(cat /var/db/pkg/app-text/docbook-sgml-dtd-*/SLOT | sed 's:^:/etc/sgml/sgml-docbook-:; s:$:.cat:')
#    /etc/sgml/sgml.cenv
#    /etc/sgml/sgml.env"
# has_version app-text/sgml-common \
#    && PRUNE="${PRUNE}
#    /etc/sgml/sgml-ent.cat
#    /etc/sgml/sgml-docbook.cat
#    /etc/sgml/catalog"
# has_version app-text/sgmltools-lite \
#    && PRUNE="${PRUNE}
#    /etc/env.d/93sgmltools-lite
#    /etc/sgml/sgml-lite.cat"
# has_version kde-base/kdebase \
#    && PRUNE="${PRUNE}
#    /var/log/kdm.log
#    /var/run/kdm.pid
# has_version net-fs/nfs-utils \
#    && PRUNE="${PRUNE}
#    $(echo /var/lib/nfs/{sm,sm.bak,state})
#    /var/run/rpc.statd.pid"
# has_version net-misc/dhcpcd \
#    && PRUNE="${PRUNE}
#    /etc/ntp.conf		/etc/ntp.conf.sv
#    /etc/resolv.conf        /etc/resolv.conf.sv
#    /etc/yp.conf    	/etc/yp.conf.sv
#    $(eval echo /var/cache/dhcpcd-{$interfaces}.cache)
#    $(eval echo /var/lib/dhcpc{,/dhcpcd{.exe,-{$interfaces}.info{,.old}}})
#    $(eval echo /var/run/dhcpcd-{$interfaces}.pid)"
# has_version net-misc/ntp \
#    && PRUNE="${PRUNE}
#    /etc/ntp.conf
#    /var/log/ntp.log"
# has_version net-misc/ssh || has_version net-misc/openssh \
#    && PRUNE="${PRUNE}
#    $(echo /etc/ssh/ssh_host_{,dsa_,rsa_}key{,.pub})"
# has_version net-misc/openssh \
#    && PRUNE="${PRUNE}
#    $(echo /etc/ssh /etc/ssh/{moduli,ssh_config,sshd_config} )"
# has_version net-print/cups \
#    && PRUNE="${PRUNE}
#    /etc/cups
#    /etc/printcap
#    /var/log/cups
#    /var/spool/cups"
# has_version '=net-www/apache-2*' \
#    && PRUNE="${PRUNE}
#    /var/lib/dav
#    /var/log/apache2
#    /var/cache/apache2
#    $(echo /etc/apache2/{conf/{ssl,vhosts},{extra,}modules,lib,logs})"
# has_version sys-apps/baselayout	\
#    && PRUNE="${PRUNE}
#    /etc/env.d/02locale
#    /etc/gentoo-release
#    /etc/modprobe.conf      /etc/modprobe.conf.old
#    /etc/modprobe.devfs     /etc/modprobe.devfs.old
#    /etc/modules.conf       /etc/modules.conf.old
#    /etc/ld.so.conf
#    /etc/prelink.conf
#    /etc/profile.env
#    /etc/csh.env
#    /usr/share/man/.keep.gz
#    /var/lib/init.d"
# has_version sys-apps/portage \
#    && PRUNE="${PRUNE}
#    $([[ -r /etc/dispatch-conf.conf ]] && sed 's/[[:space:]]*archive-dir=\("\?\)\(.*\)\1$/\2/;t;d' /etc/dispatch-conf.conf)
# has_version sys-apps/util-linux \
#    && PRUNE="${PRUNE}
#    /var/log/wtmp
#    /var/run/utmp"
# has_version sys-devel/gcc \
#    && PRUNE="${PRUNE}
#    /etc/env.d/05gcc
#    /etc/env.d/gcc/config
#    $(gcc -v 2>&1 | sed 's/.*--infodir=\([^[:space:]]*\)\>.*/\1/;t;d')"
# has_version sys-kernel/genkernel \
#    && PRUNE="${PRUNE}
#    /etc/kernels
#    /var/log/genkernel.log"
# has_version sys-libs/glibc \
#    && PRUNE="${PRUNE}
#    /etc/ld.so.cache
#    /etc/ld.so.preload
#    /etc/locales.build
#    /usr/lib/gconv/gconv-modules.cache
#    /var/run/nscd
#    $(sed 's/^[[:space:]]*logfile[[:space:]]\+\([[:graph:]]\+\)/\1/;t;d' /etc/nscd.conf)"
# has_version x11-base/opengl-update \
#    && PRUNE="${PRUNE}
#    /etc/env.d/03opengl
#    /usr/lib/libGL.a	/usr/lib/libGL.so	/usr/lib/libGL.so.1
#    /usr/X11R6/lib/libGL.so.1
#    /usr/X11R6/lib/libMesaGL.so
#    /usr/lib/libGLcore.so	/usr/lib/libGLcore.so.1
#    /usr/lib/libGL.la
#    /usr/X11R6/lib/modules/extensions/libglx.so
#    /usr/X11R6/lib/modules/extensions/libglx.a"
# has_version x11-base/xfree || has_version x11-base/xorg-x11 \
#    && PRUNE="${PRUNE}
#    /etc/X11/XF86Config-4
#    /etc/X11/xinit/.Xmodmap
#    /etc/X11/Xmodmap
#    /var/lib/xdm/authfiles
#    /var/log/xdm.log
#    /var/run/xauth
#    /var/run/xdmctl"
# has_version x11-base/xorg-x11 \
#    && PRUNE="${PRUNE}
#    /etc/X11/xorg.conf
#    $(fontdir {TTF,ttf,ukr,misc,util,75dpi,Type1,local,Speedo,encodings,encodings/large,100dpi,cyrillic,default})
#    $(echo /var/log/Xorg.*.log{,.old})"
#
# # Packages which omit ldconfig symlinks (to test, delete the symlink and see
# # if ldconfig recreates it). Specify at least to minor, these are ugly.
# has_version '=media-video/ati-drivers-3.9*' \
#    && PRUNE="${PRUNE}	/usr/X11R6/lib/libfglrx_gamma.1"
# has_version '=media-video/nvidia-glx-1.0*' \
#    && PRUNE="${PRUNE} 	/usr/lib/libXvMCNVIDIA_dynamic.so.1"
# has_version '=net-nds/openldap-2.2.26*' \
#    && PRUNE="${PRUNE} 	/usr/lib/liblber.so.2 /usr/lib/libldap.so.2 /usr/lib/libldap_r.so.2"
#
# # kernel modules - also /lib/modules/$(uname -r) above.
# has_version virtual/linux-sources \
#    && for kernel in $(grep -l '\<virtual/linux-sources\>' /var/db/pkg/sys-kernel/*/PROVIDE); do
#    	PRUNE="${PRUNE} $(bunzip2 -c "${kernel%/PROVIDE}/environment.bz2" | sed 's:^KV=:/lib/modules/:;ta;d;:a;q')"
#    done
# }
#
# # Lists of files too large to become part of the find command line
# # File names should be output one-per-line on stdout
# prune_files() {
#    # syslog-ng
#    has_version app-admin/syslog-ng \
#    	&& cat /etc/syslog-ng/syslog-ng.conf \
#    	| grep -v '^[[:space:]]*\(#\|$\)' \
#    	| grep '^destination' \
#    	| sed 's/^.*file("\([^"]\+\)");.*/\1/'
#    # Tetex stuff, oh happy days.
#    if has_version app-text/tetex; then
#    	(
#    	echo /usr/bin/texi2html /usr/share/texmf/ls-R
#    	echo /etc/texmf/web2c/{texmf.cnf,fmtutil.cnf,updmap.cfg}
#    	) | sed 's/[[:space:]]\+/\n/g'
#    	expandpathsea /var/lib/texmf/ls-R /var/cache/fonts/ls-R
#    	# texlinks
#    	sed -e 's/#.*//;/^$/d;s/^ *\([^[:space:]]\+\)[[:space:]]\+.*$/\1/;s:^:/usr/bin/:' /etc/texmf/web2c/fmtutil.cnf
#    fi
#    # http://bugs.gentoo.org/show_bug.cgi?id=9849
#    if has_version sys-apps/baselayout; then
#    	[[ -r /usr/share/baselayout/mkdirs.sh ]] \
#    		&& sed 's/^.*  can.t create \(.*\)"$/\1/;t;d' \
#    			/usr/share/baselayout/mkdirs.sh
#    	echo "/var/db"
#    fi
#    has_version sys-devel/binutils-config && (
#    	TARGET="$CHOST"
#    	VER="$(source "/etc/env.d/binutils/config-$TARGET"; echo "$CURRENT")"
#    	cd "/usr/${TARGET}/binutils-bin/${VER}"
#    	for x in *; do
#    		echo "/usr/${TARGET}/bin/${x}"
#    		echo "/usr/bin/${TARGET}-${x}"
#    		echo "/usr/bin/${x}"
#    	done
#    	echo "/usr/${TARGET}/lib/ldscripts"
#    	cd "/usr/lib/binutils/${TARGET}/${VER}"
#    	for x in lib*; do
#    		echo "/usr/${TARGET}/lib/${x}"
#    	done
#    	cd "/usr/lib/binutils/${TARGET}/${VER}/include"
#    	for x in *; do
#    		echo "/usr/include/${x}"
#    	done
#    	echo "/etc/env.d/binutils/config-$TARGET"
#    	echo "/etc/env.d/05binutils"
#    )
#    if has_version sys-libs/db; then
#    	# db.eclass
#    	echo /usr/lib/libdb{,_cxx,_tcl,_java}.{so,a}
#    	[[ -f /usr/lib/libdb1.so.2 ]] && echo /usr/lib/libdb{.so.2,{,-}1.so}
#    	echo /usr/include/db{,_185}.h
#    fi | sed 's/[[:space:]]\+/\n/g'
#
#    [[ "$looking_for" ]] && return
#
#    # shared-mime-database not done in sandbox - e.g. monodevelop
#    if has_version dev-util/desktop-file-utils \
#    	|| has_version x11-misc/shared-mime-info; then
#    	for package in /usr/share/mime/packages/*.xml; do
#    		[[ -f "$package" ]] \
#    		&& sed 's!.*<mime-type[[:space:]]\+type="\([^"]*\)">.*!/usr/share/mime/\1.xml!;t;s!.*<mime-type[[:space:]]\+type='"'"'\([^'"'"']*\)'"'"'>.*!/usr/share/mime/\1.xml!;t;d' "$package"
#    	done | while read x; do
#    		echo "$x"; dirname "$x"
#    	done | sort -u
#    	cat <<END
# /usr/share/applications/mimeinfo.cache
# /usr/share/mime/aliases
# /usr/share/mime/subclasses
# END
#    fi
#    # See /etc/init.d/xfs (using my own sed-hacker get_fontdir_list)
#    [[ -e $(car /etc/runlevels/*/xfs ) ]] \
#    	&& for x in $(sed ':a;/,$/N;s/,\n//;ta;s/^[[:space:]]*catalogue[[:space:]]*=\(.*\)$/\1/;tb;d;:b;q' /etc/X11/fs/config); do
#    		fontdir $x
#    	done
#    has_version app-shells/bash-completion-config \
#    	&& for x in /etc/bash_completion.d/*; do
#    		[[ -h "$x" && -r "$x" ]] && echo "$x"
#    	done
#
#    # Explicitly referenced pidfiles
#    sed ':a;/\\$/N;s/\\\n//;ta;s/^.*start-stop-daemon.*\(\<-p\|--pidfile\>\|\<-m\|--make-pidfile\>\)[[:space:]]*\(\/[^[:space:]]\+\).*$/\2/;tb;s/^.*pidfile=\(\/[^[:space:]]\+\).*$/\1/;tb;d;:b;s/[[:space:]]\+/\n/g' /var/lib/init.d/started/*
#    # Guess some pidfiles
#    for service in /var/lib/init.d/started/*; do
#    	echo /var/run/${service##*/}.pid
#    done
#    for service in /var/lib/init.d/started/net.ppp*; do
#    	echo /var/run/${service##*/net.}.pid
#    done
#    # Custom init scripts - if it's in a runlevel, it's wanted...
#    for service in /etc/runlevels/*/*; do
#    	readlink ${service}
#    done
#    # lost+found in each mountpoint (but not files therein)
#    sed 's:[^[:space:]]\+ \(/[^[:space:]]*\).*:\1/lost+found:;ta;d;:a;s://:/:g' /proc/mounts
#    # .reiserfs_priv for reiser3 and reiser4 filesystems
#    sed 's:[^[:space:]]\+ \(/[^[:space:]]*\) reiser\(fs\|4\)\>.*:\1/.reiserfs_priv:;ta;d;:a;s://:/:g' /proc/mounts
#    # the actual /lib/modules goes here so that directories therein still
#    # get seen - TODO needs restructuring...
#    echo "/lib/modules"
#
#    if [[ -r /etc/portage/cruft.locals ]]; then
#    	echo "Adding contents of /etc/portage/cruft.locals..." >&2
#    	sed -e 's/#.*//;/^$/d' /etc/portage/cruft.locals \
#    	| while read file; do
#    		if [[ -e "$file" ]]; then
#    			echo " +  $file" >&2
#    			echo "$file"
#    		else
#    			echo " -  $file" >&2
#    		fi
#    	done
#    fi
# }

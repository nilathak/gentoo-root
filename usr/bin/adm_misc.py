#!/usr/bin/env python
# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================

# module imports
import copy
import os
import re
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
        self.parser.add_option('-f', '--force', action='store_true')
        self.parser.add_option('-o', '--options', type='string')

    def validate(self):
        super(ui, self).validate()
        if not self.opts.type:
            raise self.owner.exc_class('specify the type of operation')

class adm_misc(pylon.base.base):
    'container script for misc admin tasks'

    def run_core(self):
        getattr(self, self.__class__.__name__ + '_' + self.ui.opts.type)(self.ui.opts.options)

    def adm_misc_router(self, opts):
        # ====================================================================
        'mount router image for local administration, rsync to router when finished'

        def makedirs_if_missing(path):
            try:
                os.makedirs(path)
            except OSError as exc:
                import errno
                if exc.errno == errno.EEXIST:
                    pass
                else:
                    reraise()

        router_proj = '/mnt/work/projects/router'
        router_root = '/tmp/router'
        image = os.path.join(router_proj, 'router.img')
        router = 'belial'
        rsync_exclude = (
                         '--exclude="/boot/grub/"',  # install & configure locally
                         '--exclude="/dev/"',
                         '--exclude="/etc/mtab"',  # keep local mount table
                         '--exclude="/proc/"',
                         '--exclude="/run/"',
                         '--exclude="/sys/"',
                         '--exclude="/tmp/"',
                         '--exclude="/var/cache/"',
                         '--exclude="/var/lib/"',
                         '--exclude="/var/log/"',
                         '--exclude="/var/spool/"',
                        )
        bind_map = (
            # host                      router
            ('/dev', '/dev'),
            ('/mnt', '/mnt'),
            ('/mnt/software/linux', '/mnt/software/linux'),  # needed for exec rights
            ('/mnt/work/projects', '/mnt/work/projects'),  # needed for exec rights
            ('/proc', '/proc'),
            ('/sys', '/sys'),
            ('/usr/portage', '/usr/portage'),  # remove with new CF card
            ('/usr/src/linux', '/usr/src/linux'),  # remove with new CF card
            ('/var/cache/edb', '/var/cache/edb'),  # remove with new CF card
            ('/tmp', '/tmp'),
            ('/tmp', '/var/tmp/portage'),  # remove with new CF card
            )

        # first instance does mounting
        makedirs_if_missing(router_root)
        try:
            self.dispatch('mount | grep ' + router_root,
                          output=None, passive=True)
        except self.exc_class:
            self.dispatch('mount -o loop ' + image + ' ' + router_root,
                          output='stderr')
            for (src, dest) in bind_map:
                makedirs_if_missing(src)
                self.dispatch('mount -o bind %s %s' % (src, os.path.join(router_root, dest.strip('/'))),
                              output='stderr')

        self.ui.info('Entering the router chroot...')
        if opts:
            opts = "c '%s'" % opts
        else:
            opts = ''
        try:
            self.dispatch("env -i HOME=$HOME TERM=$TERM linux32 chroot %s /bin/bash -l%s" % (router_root, opts),
                          output='nopipes')
        except self.exc_class:
            self.ui.warning('chroot shell exited with error status (last cmd failed?)')
        self.ui.info('Leaving the router chroot...')

        # last instance does umounting
        if len([x for x in self.dispatch('ps aux | grep adm_misc.py',
                                         output=None,
                                         passive=True).stdout if x.find('t router') != -1]) == 1:
            for (src, dest) in reversed(bind_map):
                self.dispatch('umount ' + os.path.join(router_root, dest.strip('/')),
                              output='stderr')

            self.ui.info('Syncing changes to embedded device...')
            try:
                try:
                    self.dispatch('ping ' + router + ' -c 1',
                                  output='stderr')
                    dry_run = 'n'
                    if self.ui.opts.force:
                        dry_run = ''
                    try:
                        self.dispatch('rsync -aHv' + dry_run + ' --delete ' + router_root + '/ ' + router + ':/ ' + ' '.join(rsync_exclude),
                                      output='both')
                        if not self.ui.opts.force:
                            self.ui.info('The router files above will be lost after the rsync! OK? Use the force then ;)...')
                        else:
                            self.ui.info('Updating grub in native environment...')
                            self.dispatch('ssh %s grub-install /dev/sda' % (router),
                                          output='both')
                            self.dispatch('ssh %s grub-mkconfig -o /boot/grub/grub.cfg' % (router),
                                          output='both')
                    except self.exc_class:
                        self.ui.warning('Something went wrong during the sync process...')
                except self.exc_class:
                    self.ui.warning('Embedded device is offline, changes are NOT synced...')
            finally:
                self.dispatch('umount ' + router_root,
                              output='stderr')
        else:
            self.ui.warning('No other router chroot environment should be open while doing rsync, close them...')

    def adm_misc_wake(self, opts):
        # ====================================================================
        'wake hosts via wake-on-lan (give hostname via options switch)'
        mac_dict = {'baal':   '00:13:D4:06:88:B6',
                    'diablo': '00:14:6C:32:CA:1B'
                    }
        host = opts

        for i in range(0, 10):
            try:
                self.dispatch('ether-wake -i eth%i %s' % (i, mac_dict[host]),
                              output=None)
                self.ui.info('Sent magic packet on eth%i' % i)
            except self.exc_class:
                pass

        import threading
        ev = threading.Event()
        self.dispatch(lambda host=host, ev=ev: self.wake_polling(host, ev),
                      blocking=False)
        self.dispatch(lambda host=host, ev=ev: self.wake_polling(host, ev, 150),
                      blocking=False)
        self.join()

    def wake_polling(self, host, ev, timeout=None):
        if timeout:
            ev.wait(timeout)
            ev.set()
        else:
            while not ev.is_set():
                try:
                    if host == 'baal':
                        self.dispatch('showmount -e %s' % host,
                                      output=None)
                except self.exc_class:
                    pass
                else:
                    ev.set()

    def adm_misc_check_rights(self, opts):
        # ====================================================================
        'set access rights on fileserver'

        # tested also with Dropbox softlink targets
        public = (
            '/mnt/audio',
            '/mnt/docs',
            '/mnt/games',
            '/mnt/software',
            '/mnt/video',
            )
        self.ui.info('Setting rights for public data...')
        for p in public:
            self.dispatch(lambda p=p: self.set_rights(p),
                          blocking=False)
        self.join()

        private = (
            '/mnt/images',
            '/mnt/work',
            )
        self.ui.info('Setting rights for private data...')
        for p in private:
            self.dispatch(lambda p=p: self.set_rights(p, dirmask=0o700, filemask=0o600),
                          blocking=False)
        self.join()

    def set_rights_dir(self, dir, owner, group, dirmask):
        os.chown(dir, owner, group)
        os.chmod(dir, dirmask)

    def set_rights_file(self, file, owner, group, filemask):
        os.chown(file, owner, group)
        os.chmod(file, filemask)

    def set_rights(self, tree,
                   owner=1000,  # schweizer
                   group=100,  # users
                   dirmask=0o750,
                   filemask=0o640):
        dir_exceptions = (
            '/mnt/software/linux',
            '/mnt/work/projects',
            )
        file_exceptions = (
            '/mnt/images/private/hannes',
            '/mnt/video/private/hannes',
            )
        for root, dirs, files in os.walk(tree, onerror=lambda x: self.ui.error(str(x))):
            for d in copy.copy(dirs):
                if os.path.join(root, d) in dir_exceptions:
                    dirs.remove(d)
            for f in copy.copy(files):
                if os.path.join(root, f) in file_exceptions:
                    files.remove(f)
            if not self.ui.opts.dry_run:
                for d in dirs:
                    self.set_rights_dir(os.path.join(root, d), owner, group, dirmask)
                for f in files:
                    self.set_rights_file(os.path.join(root, f), owner, group, filemask)

#    def media_pdf(self, opts=None):
#        'embed OCR text in scanned PDF file'
#
#        if not opts:
#            raise self.exc_class('give a pdf filename via -o switch')
#        pdf_file = opts
#        pdf_base = os.path.splitext(pdf_file)[0]
#
#        self.ui.ext_info('Extracting pages from scanned PDF...')
#        self.dispatch('pdfimages %s %s' % (pdf_file, pdf_base),
#                      output='stderr')
#
#        # determine list of extracted image files
#        # FIXME
#        # check if extracted images are in A4 format (pdfimages
#        # does not care if a pdf was scanned or not, this may lead to
#        # extracted logo images and such stuff)
#        import glob
#        images = glob.iglob('%s-[0-9]*.ppm' % pdf_base)
#
#        for image in images:
#            ppm_base = os.path.splitext(image)[0]
#            tif_file = ppm_base + '.tif'
#
#            self.ui.ext_info('Apply threshold to %s...' % ppm_base)
#            # FIXME find optimal threshold value for OCR (test with
#            # multiple pdfs)
#            # FIXME what is this threshold value? color space?
#            self.dispatch('convert %s -threshold 50000 %s' % (image, tif_file),
#                          output='stderr')
#
#            self.dispatch('TESSDATA_PREFIX=/usr/share/ tesseract %s %s -l deu' % (tif_file, ppm_base),
#                          output='stderr')
#
#            # FIXME
#            # embed media text into pdf file
#

    def adm_misc_check_audio(self, opts):
        # ====================================================================
        'check audio metadata (low bitrates, ...)'
        if not opts:
            opts = '/mnt/audio'

        # FIXME ignore single low bitrate if other songs in album are
        # okay, or simply ignore low bitrate VBR files

        lossy_extensions = re.compile(r'\.mp3$|\.ogg$', re.IGNORECASE)

        dir_exceptions = (
            '/mnt/audio/0_sort',
            )
        file_exceptions = (
            )
        for root, dirs, files in os.walk(opts, onerror=lambda x: self.ui.error(str(x))):
            for d in copy.copy(dirs):
                if os.path.join(root, d) in dir_exceptions:
                    dirs.remove(d)
            for f in copy.copy(files):
                if os.path.join(root, f) in file_exceptions:
                    files.remove(f)
            for f in files:
                name = os.path.join(root, f)
                if lossy_extensions.search(name):
                    out = self.dispatch('exiftool "%s" | grep -i "Audio Bitrate\\|Nominal Bitrate"' % name,
                                        output=None).stdout[0]
                    # ignore unit specification
                    bitrate = float(out.split()[-2])
                    if bitrate < 130:
                        self.ui.warning('Low audio bitrate detected: (%-6d) %s' % (bitrate, name))

    def adm_misc_check_images(self, opts):
        # ====================================================================
        'check image metadata (silently convert to xmp)'
        if not opts:
            opts = '/mnt/images'

        # - convert existing metadata to xmp, while deleting all
        #   metadata which cannot be converted to xmp.
        # - repair broken metadata structures
        self.dispatch('exiftool -q -r -P -overwrite_original -all= "-all>xmp:all" "%s"' % (opts))

        # - renaming for scanned images:
        #   rename files according to creationdate -> even ok for
        #   scanned pics, as the order in which they were scanned is
        #   often the chronological order in the paper album
        #   - change filename according to createdate or modifydate
        #     (they should be the same at this stage)
        #   - manually change createdate to actual date of paper
        #     picture if necessary
        # - renaming for camera images:
        #   - change filename according to createdate
        #   - if there are pics without a createdate:
        #     exiftool -r -P -overwrite_original '-FileModifyDate>xmp:CreateDate' <file>
        dir_exceptions = (
            '/mnt/images/0_sort',
            '/mnt/images/cartoons',
            '/mnt/images/cuteness',
            '/mnt/images/design',
            '/mnt/images/fun',
            '/mnt/images/private',
            )
        for root, dirs, files in os.walk(opts, onerror=lambda x: self.ui.error(str(x))):
            for d in copy.copy(dirs):
                if os.path.join(root, d) in dir_exceptions:
                    dirs.remove(d)
            for d in dirs:
                dir_from_album_root = os.path.join(root, d).replace(opts, '').strip('/')
                dir_wo_metachars = dir_from_album_root.replace('/', '_').replace(' ', '_')
                self.dispatch('exiftool -q -P "-FileName<CreateDate" -d "%s_%%Y-%%m-%%d_%%H-%%M-%%S%%%%-c.%%%%e" "%s"' % (dir_wo_metachars, os.path.join(root, d)))
            # check for missing CreateDate tag
            for f in files:
                if len(self.dispatch('exiftool -CreateDate "%s"' % (os.path.join(root, f)),
                                     None).stdout) == 0:
                    self.ui.warning('Missing CreateDate tag for: ' + os.path.join(root, f))

        # MANUAL FIX QUICK REFERENCE
        # - add xmp standard caption for digikam viewing
        #   exiftool -P -overwrite_original -xmp:Description="caption for digikam" <file>
        # - change creation date
        #   exiftool -P -overwrite_original -xmp:CreateDate="1990:02:01 00:00:00" <file>
        # - check for any existing EXIF / IPTC / XMP metadata
        #   exiftool -a -G1 * | grep -v ExifTool | grep -v System | grep -v File | grep -v Composite | grep -v PNG | grep -v =======

        # FIXME do I still need this command?
        # exiftool -r -P '-FileName<ModifyDate' -d %Y-%m-%d_%H-%M-%S%%-c.%%e <file>


    def adm_misc_check_work(self, opts):
        # ====================================================================
        'check data consistency on work'
        if not opts:
            opts = '/mnt/work'
        sidecar_pdf_expected = re.compile(r'\.doc$|\.nb$|\.ppt$|\.vsd$|\.xls$', re.IGNORECASE)
        sidecar_pdf_wo_extension_expected = re.compile(r'exercise.*\.tex$', re.IGNORECASE)
        dir_exceptions = (
            '/mnt/work/0_sort',
            '/mnt/work/projects/backup',
            '/mnt/work/docs/education/thesis/competition',
            )
        file_exceptions = (
            )
        for root, dirs, files in os.walk(opts, onerror=lambda x: self.ui.error(str(x))):
            for d in copy.copy(dirs):
                if os.path.join(root, d) in dir_exceptions:
                    dirs.remove(d)
            for f in copy.copy(files):
                if os.path.join(root, f) in file_exceptions:
                    files.remove(f)
            for f in files:
                sidecar = f + '.pdf'
                sidecar_wo_extension = os.path.splitext(f)[0] + '.pdf'
                if (sidecar_pdf_expected.search(f) and not sidecar in files or
                    sidecar_pdf_wo_extension_expected.search(f) and not sidecar_wo_extension in files):
                    self.ui.warning('Sidecar PDF expected for: ' + os.path.join(root, f))

    def adm_misc_check_ssd(self, opts):
        # ====================================================================
        'check various SSD health parameters'
        ssd_mount_points = (
            '/',
            )
        for mp in ssd_mount_points:
            self.dispatch('/sbin/fstrim -v ' + mp)

    def adm_misc_check_filetypes(self, opts):
        # ====================================================================
        'check for expected/unexpected filetypes on fileserver'
        allowed = {
            'audio': re.compile(r'\.flac$|\.mp3$|\.ogg$|cover\.jpg$', re.IGNORECASE),
            'images': re.compile(r'\.gif$|\.jpg$|\.png$', re.IGNORECASE),
            'video': re.compile(r'\.avi$|\.bup$|\.flv$|\.ifo$|\.img$|\.iso$|\.jpg$|\.m2ts$|\.mkv$|\.mp4$|\.mpg$|\.nfo$|\.ogm$|\.srt$|\.sub$|\.vob$', re.IGNORECASE),
            }
        for k in allowed.keys():
            dir_exceptions = (
                '/mnt/audio/0_sort',
                '/mnt/images/0_sort',
                '/mnt/video/0_sort',
                )
            file_exceptions = (
                )
            for root, dirs, files in os.walk(os.path.join('/mnt', k), onerror=lambda x: self.ui.error(str(x))):
                for d in copy.copy(dirs):
                    if os.path.join(root, d) in dir_exceptions:
                        dirs.remove(d)
                for f in copy.copy(files):
                    if os.path.join(root, f) in file_exceptions:
                        files.remove(f)
                for f in files:
                    name = os.path.join(root, f)
                    if not allowed[k].search(name):
                        self.ui.warning('Unexpected filetype detected: ' + name)

    def adm_misc_check_filenames(self, opts):
        # ====================================================================
        'check for names incompatible with other filesystems'
        if not opts:
            opts = '/mnt'
        ntfs_exceptions = re.compile(r'\0|\\|:|\*|\?|"|<|>|\|')
        dir_exceptions = (
            '/mnt/audio/0_sort',
            '/mnt/docs/0_sort',
            '/mnt/games/0_sort',
            '/mnt/images/0_sort',
            '/mnt/video/0_sort',
            '/mnt/work/0_sort',
            '/mnt/work/projects/backup',
            )
        file_exceptions = (
            )
        for root, dirs, files in os.walk(opts, onerror=lambda x: self.ui.error(str(x))):
            for d in copy.copy(dirs):
                if os.path.join(root, d) in dir_exceptions:
                    dirs.remove(d)
            for f in copy.copy(files):
                if os.path.join(root, f) in file_exceptions:
                    files.remove(f)
            names = copy.copy(dirs)
            names.extend(files)
            names_lower = [x.lower() for x in names]
            lower_dict = dict.fromkeys(names_lower, 0)
            for name in names_lower:
                lower_dict[name] += 1
            for name in names:
                if ntfs_exceptions.search(name):
                    self.ui.warning('NTFS incompatible filesystem object: ' + os.path.join(root, name))
                if lower_dict[name.lower()] > 1:
                    self.ui.warning('Filesystem objects only distinguished by case: ' + os.path.join(root, name))

    def adm_misc_check_raid(self, opts):
        # ====================================================================
        'check for bad blocks on raid'

        self.ui.info('Starting software RAID consistency check...')
        self.dispatch('echo check >> /sys/block/md1/md/sync_action',
                      output='stderr')
        self.dispatch('echo check >> /sys/block/md2/md/sync_action',
                      output='stderr')
        self.dispatch('echo check >> /sys/block/md3/md/sync_action',
                      output='stderr')

        # FIXME poll /proc/mdstat until completion, then
        # report /sys/block/md*/md/mismatch_cnt

    def adm_misc_shutdown_server(self, opts):
        # ====================================================================
        'shutdown server if all clients have disconnected'

        # first measurement
        if not self.is_server_busy():
            # add some hysteresis to allow:
            # - save dual booting
            # - save fcron startup
            import time
            time.sleep(10 * 60)

            # second measurement
            if not self.is_server_busy():
                self.ui.debug('LAN seems to be empty -> shutting down homeserver...')
                self.dispatch('/sbin/shutdown -h now',
                              output=None)

    def is_server_busy(self):
        # scan openvpn subnet as well to avoid shutdown during remote access
        nmap_output = self.dispatch('nmap --exclude baal,belial -sP 192.168.0.0/24 10.8.0.2-255 | egrep "hosts? up" | sed "s/.*(\([0-9]*\).*/\\1/"',
                                    passive=True,
                                    output=None).stdout
        # ignore failed resolution of exceptions if router is down by
        # just evaluating last line
        active_hosts = int(nmap_output[-1])
        if active_hosts == 0:
            # our LAN is empty, but first ensure we don't turn off a
            # busy server
            load_avg_line = self.dispatch('cat /proc/loadavg',
                                          passive=True,
                                          output=None).stdout[0]
            load_5min_avg = float(load_avg_line.split()[2])
            if load_5min_avg < 0.7:
                return False
        return True

    def adm_misc_graphtool(self, opts):
        # FIXME
        # - test graph_tool.topology.is_DAG

        import graph_tool.all as gt
        import random

        # input
        #################################################
        # - merge UML communication diagrams for mission modes
        # - generate interesting set of random graphs
        # - UML sanity check
        #   - loops? graph_tool.stats.label_self_loops
        g = gt.graph_tool.generation.random_graph(16,
                                                  lambda:(random.choice(range(0, 4)),
                                                          random.choice(range(0, 10))),
                                                  directed=True,

                                                  # only one channel for each pair, increase capacity by new "routability" vertices
                                                  # parallel_edges=True,

                                                  verbose=True
                                                  )
        gt.graph_draw(g, output="/tmp/graph-draw.pdf")

        # 2D mapping
        #################################################
        # - compare against the "ideal" case: graph_tool.topology.shortest_path
        # - find unsuitable patterns using graph_tool.topology.subgraph_isomorphism
        # - replace them
        # - check if 2D mapping is done by using graph_tool.topology.is_planar
        lattice = gt.graph_tool.generation.lattice([4, 4])
        lattice.set_directed(True)
        gt.graph_draw(lattice, output="/tmp/graph-lattice.pdf")

        # locality optimization
        #################################################
        # - try graph_tool.topology.max_cardinality_matching
        # - determine leaf nodes maybe helpful? graph_tool.topology.max_independent_vertex_set
        # - detect routing hot-spots: graph_tool.topology.kcore_decomposition

        # routability
        #################################################
        # - check with graph_tool.topology.transitive_closure
        #

if __name__ == '__main__':
    app = adm_misc(job_class=gentoo.job.job,
                   ui_class=ui)
    app.run()

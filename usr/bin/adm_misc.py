#!/usr/bin/env python3
# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================

import copy
import multiprocessing
import os
import re
import gentoo.job
import gentoo.ui
import pylon.base

class ui(gentoo.ui.ui):
    def __init__(self, owner):
        super().__init__(owner)
        self.parser_common.add_argument('-o', '--options',
                                        help='pass custom string to operations')
        self.init_op_parser()
        self.parser_kernel.add_argument('-f', '--force', action='store_true')
        self.parser_kernel.add_argument('--no-backup', action='store_true', help='do not overwrite keyring content with backup')
        self.parser_kernel.add_argument('-s', '--small', action='store_true', help='skip rsync of large ISOs')
        self.parser_wrap.add_argument('-f', '--force', action='store_true')

    def setup(self):
        super().setup()
        if not self.args.op:
            raise self.owner.exc_class('Specify at least one subcommand operation')
        
class adm_misc(pylon.base.base):
    'container script for misc admin tasks'

    def run_core(self):
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()

    def adm_misc_kernel(self):
        # ====================================================================
        'scripting stuff to build kernels'

        self.dispatch('eselect kernel list', passive=True)
        selection = input('which kernel?')
        self.dispatch('eselect kernel set ' + selection)
        os.chdir('/usr/src/linux')
        self.dispatch('make -j' + str(multiprocessing.cpu_count()*2-1), output='nopipes')

        # install kernel to USB keyrings
        try:
            self.dispatch('mkdir /tmp/keyring', output='stdout')
        except self.exc_class:
            pass
        try:
            self.dispatch('rm -rf /boot; ln -s /tmp/keyring/' + self.ui.hostname + ' /boot', output='stdout')
        except self.exc_class:
            pass

        part = self.dispatch('findfs LABEL=KEYRING', passive=True, output=None).stdout[0]
        dev = part[:-1]

        # umount KDE mounts as well, since parallel mounts might lead to ro remounting 
        try:
            while True:
                self.dispatch('umount ' + part, passive=True, output='stdout')
        except self.exc_class:
            pass

        self.ui.info('perform automatic offline fsck')
        try:
            self.dispatch('fsck.vfat -a ' + part)
        except self.exc_class:
            pass

        self.dispatch('mount ' + part + ' /tmp/keyring')
        self.dispatch('make install')

        self.ui.info('install grub modules + embed into boot sector')
        self.dispatch('grub-install ' + dev + ' --boot-directory=/tmp/keyring/boot')
        if not self.ui.args.no_backup:
            self.ui.info('rsync new grub installation to keyring backup')
            self.dispatch('rsync -a /tmp/keyring/boot /mnt/work/projects/usb_boot --exclude="/boot/grub/grub.cfg"',
                          output='both')
        self.ui.info('install host-specific grub.cfg (grub detects underlying device and correctly uses absolute paths to kernel images)')
        self.dispatch('grub-mkconfig -o /boot/grub/grub.cfg')

        if not self.ui.args.no_backup:
            self.ui.info('rsync keyring backup to actual device')
            rsync_exclude = [
                # kernels & grub.cfgs
                '--exclude="/diablo"',

                # convenience key
                '--exclude="/key"',
            ]
            rsync_exclude_small = [
                '--exclude="/*.iso"',
                '--exclude="/sources"',
            ]
            if self.ui.args.small:
                rsync_exclude.extend(rsync_exclude_small)
            dry_run = ''
            if not self.ui.args.force:
                self.ui.info('Use -f to apply sync!')
                dry_run = 'n'
            self.dispatch('rsync -av' + dry_run + ' --modify-window 1 --no-perms --no-owner --no-group --inplace --delete /mnt/work/projects/usb_boot/ /tmp/keyring ' + ' '.join(rsync_exclude),
                      output='both')

        try:
            while True:
                self.dispatch('umount ' + part, passive=True, output='stdout')
        except self.exc_class:
            pass

        self.dispatch('rm /boot')
        self.ui.info('Rebuild kernel modules')
        self.dispatch('make modules_install')
        self.dispatch('emerge @module-rebuild', output='nopipes')

    def adm_misc_rotate_wpa(self):
        # ====================================================================
        'renew random guest access key for wlan'
        # FIXME
        pass

    def adm_misc_wrap(self):
        # ====================================================================
        'mount wrap image for local administration'

        # FIXME
        #Loading Linux 3.18.7-gentoo ...
        #CPU: vendor_id 'Geode by NSC' unknown, using generic init.
        #CPU: Your system may be unstable.
        #i8042: No controller found
        #sc1200wdt: io parameter must be specified
        #mce: Unable to init device /dev/mcelog (rc: -5)
        
        # Notice: NX (Execute Disable) protection missing in CPU!
        # DMI not present or invalid.
        # raid6: mmxx1       35 MB/s
        # raid6: mmxx2       58 MB/s
        # raid6: int32x1     15 MB/s
        # raid6: int32x2     23 MB/s
        # raid6: int32x4     23 MB/s
        # raid6: int32x8     23 MB/s
        # raid6: using algorithm mmxx2 (58 MB/s)
        # raid6: using intx1 recovery algorithm
        #  
        #  * Setting console font [lat9w-16] ...
        #  [ ok ]
        # getfont: KDFONTOP: No space left on device
        #  
        def makedirs_if_missing(path):
            try:
                os.makedirs(path)
            except OSError as exc:
                import errno
                if exc.errno == errno.EEXIST:
                    pass
                else:
                    raise exc
     
        image = '/mnt/work/projects/led_cube/WRAP.1E.img'
        local = '/tmp/wrap'
        device = 'belial'
        rsync_exclude = (
            '--exclude="/boot/grub/"',  # install & configure locally
            '--exclude="/etc/resolv.conf"',  # do not interfer with dhcp on device
            '--exclude="/etc/ssh/ssh_host*"',  # do not overwrite ssh host key
            '--exclude="/dev/"',
            '--exclude="/proc/"',
            '--exclude="/run/"',
            '--exclude="/sys/"',
            '--exclude="/tmp/"',
            '--exclude="/var/dhcpcd/dhcpcd-wlan0.lease"',
            '--exclude="/var/lib/ntpd.drift"',
            '--exclude="/var/lib/postfix/master.lock"',
            '--exclude="/var/lib/syslog-ng/syslog-ng.persist"',
            '--exclude="/var/log/"', # services will only run on the actual device
            '--exclude="/var/spool/"', # cron, postfix
        )
        bind_map = (
            # local, device
            ('/dev', '/dev'),
            ('/dev/pts', '/dev/pts'),
            ('/mnt', '/mnt'), # without games & video
            ('/proc', '/proc'),
            ('/sys', '/sys'),
            ('/tmp', '/tmp'),
            )
     
        # first instance does mounting
        makedirs_if_missing(local)
        try:
            self.dispatch('mount | grep ' + local,
                          output=None, passive=True)
        except self.exc_class:
            self.dispatch('mount ' + image + ' ' + local,
                          output='stderr')
            for (src, dest) in bind_map:
                makedirs_if_missing(src)
                self.dispatch('mount -o bind {0} {1}'.format(src, os.path.join(local, dest.strip('/'))),
                              output='stderr')
     
        self.ui.info('Entering the chroot...')

        # run batch commands
        opts = ''
        if self.ui.args.options:
            opts = "c '{0}'".format(self.ui.args.options)

        try:
            # chname for chroot hostname modification needs CONFIG_UTS_NS=y
            self.dispatch("env -i HOME=$HOME $HOSTNAME={0} TERM=$TERM chname {0} linux32 chroot {1} /bin/bash -l{2}".format(device, local, opts),
                          output='nopipes')
        except self.exc_class:
            self.ui.warning('chroot shell exited with error status (last cmd failed?)')
        self.ui.info('Leaving the chroot...')
     
        # last instance does umounting
        if len([x for x in self.dispatch('ps aux | grep adm_misc.py',
                                         output=None,
                                         passive=True).stdout if ' wrap' in x]) == 1:
            for (src, dest) in reversed(bind_map):
                self.dispatch('umount ' + os.path.join(local, dest.strip('/')),
                              output='stderr')
     
            self.ui.info('Syncing changes to device...')
            try:
                try:
                    self.dispatch('ping ' + device + ' -c 1',
                                  output='stderr')
                    dry_run = 'n'
                    if self.ui.args.force:
                        dry_run = ''
                    try:
                        self.dispatch('rsync -aHv' + dry_run + ' --delete ' + local + '/ ' + device + ':/ ' + ' '.join(rsync_exclude),
                                      output='both')
                        if not self.ui.args.force:
                            self.ui.info('The device files above will be lost after the rsync! OK? Use the force then ;)...')
                        else:
                            self.ui.info('Updating grub in native environment...')
                            self.dispatch('ssh {0} grub-install /dev/sda'.format(device),
                                          output='both')
                            self.dispatch('ssh {0} grub-mkconfig -o /boot/grub/grub.cfg'.format(device),
                                          output='both')
                    except self.exc_class:
                        self.ui.warning('Something went wrong during the sync process...')
                except self.exc_class:
                    self.ui.warning('Device is offline, changes are NOT synced...')
            finally:
                self.dispatch('umount ' + local,
                              output='stderr')
        else:
            self.ui.warning('No other device chroot environment should be open while doing rsync, close them...')

    def adm_misc_check_ssd(self):
        # ====================================================================
        'periodic manual SSD maintenance'
        ssd_mount_points = {
            'diablo': (
                '/',
            ),
        }
        for mp in ssd_mount_points[self.ui.hostname]:
            self.dispatch('/sbin/fstrim -v ' + mp)

    def adm_misc_check_btrfs(self):
        # ====================================================================
        'periodic manual btrfs maintenance'

        btrfs_devices = {
            'diablo': (
                '/dev/mapper/cache0',
                '/dev/mapper/pool0',
            ),
            'belial': (
                '/dev/sda1',
            ),
        }

        # scrubbing
        # =========
        # - btrfs check on unmounted filesystem only necessary if btrfs scrub shows errors
        # - Data blocks are not duplicated unless you have RAID1 or higher, but they are checksummed
        # - Scrub will therefore know if your metadata is corrupted and typically correct it on its own
        # - It can also tell you if your data blocks got corrupted, auto fix them if RAID allows, or report them to you in syslog otherwise.
        for bd in btrfs_devices[self.ui.hostname]:
            self.dispatch('/sbin/btrfs scrub start -BR ' + bd)

        # balance
        # =======
        # - the balance command can do a lot of things (eg, also adding devices in RAID configuration),
        #   here we use it to reclaim back the space of the underused chunks so it can be allocated again according to current needs
        # - The point is to prevent some corner cases where it's not possible to allocate new metadata chunks because the whole device
        #   space is reserved for all the chunks, although the total space occupied is smaller.
        # - at least free chunks are now automatically freed with 3.18+ (eg, btrfs balance start -dusage=0)
        # - IN GENERAL: rebalancing recommended if used values are nearing total values (btrfs fi usage <mp>)
        #   Data,single: Size:225.82GiB, Used:195.36GiB
        #      /dev/mapper/cache0    225.82GiB
        #    
        #   Metadata,DUP: Size:3.50GiB, Used:1.97GiB
        #      /dev/mapper/cache0      7.00GiB
        #    
        #   System,DUP: Size:32.00MiB, Used:48.00KiB
        #      /dev/mapper/cache0     64.00MiB
        
        # ct recommended command which keeps data relocation to a minimum: btrfs balance start -v -musage=50 -dusage=50 <mp>
        # if balance command bails out with ENOSPC error on a nearly full device, use -musage=0 -dusage=0 to delete unused chunks first
        # the bigger the -dusage value, the more work balance will have to do (taking fuller and fuller blocks and trying to free them up by putting their data elsewhere)

        # stack overflow recommendation: btrfs balance start -dusage=20 <mp>
        # https://stackoverflow.com/questions/22286618/massive-btrfs-performance-degradation

        # check command duration for different -dusage percentages using: btrfs balance status
        
        # defrag
        # ======
        # A copy-on-write filesystem maintains many changes of a single file, which is helpful for snapshotting and other advanced features,
        # but can lead to fragmentation with some workloads.
        # If you have a virtual disk image, or a database file that gets written randomly in the middle, Copy On Write is going to cause many
        # fragments since each write is a new fragment (eg, virtualbox images can grow 100,000 fragments quickly). You can turn off COW for specific
        # files and directories with chattr -C /path (new files will inherit this).
        # Do not enable autodefrag or run defrag manually on specific folders, since it will break the reflink (COW) property of snapshots
        # within the volume to be defragged. this will practically double the space needed for each snapshot.

        # OPTIONAL SPEEDUP
        # btrfs filesystem defragment vbox.vdi could take hours
        # cp -reflink=never vbox.vdi vbox.vdi.new; rm vbox.vdi
        # is much faster

        # defrag home folder which contains highly fragmented databases & VM images, increased space usage for 1 month worth of snapshots should be OK.
        #btrfs_defrag_folders = {
        #    home directory, or directory which high number of extents:
        #    find / -xdev -type f -print0 | sed 's/\(.*\)/"\1"/' | xargs -0 filefrag | grep -v ' 0 extent' | grep -v ' 1 extent' | grep -v ' 2 extent'
        #}

    def adm_misc_spindown(self):
        # ====================================================================
        'force large HDD into standby mode'

        import time
        luks_uuid = 'ab45cdb1-643b-4b29-8448-f68fa65f574e'
        mounts = (
            '/mnt/games',
            '/mnt/video',
        )

        frequency_per_hour = 4
        
        # script will be run via cron.hourly
        for i in range(frequency_per_hour):

            self.ui.debug('checking for ongoing IO operations using a practical hysteresis')
            try:
                device = os.path.basename(self.dispatch('findfs UUID=' + luks_uuid,
                                                        passive=True,
                                                        output=None).stdout[0])
                io_ops_1st = self.dispatch('cat /proc/diskstats | grep ' + device,
                                           passive=True,
                                           output=None).stdout
            except self.exc_class:
                raise self.exc_class("Container HDD not found!")
            
            # 60s buffer to next run-crons job
            time.sleep(60 / frequency_per_hour * 59)
            
            io_ops_2nd = self.dispatch('cat /proc/diskstats | grep ' + device,
                                       passive=True,
                                       output=None).stdout

            if io_ops_1st[0] == io_ops_2nd[0]:
                self.ui.debug('Ensure filesystem buffers are flushed')
                for m in mounts:
                    self.dispatch('btrfs filesystem sync ' + m,
                                  output=None)
                self.ui.debug('Spinning down...')
                self.dispatch('hdparm -y /dev/' + device,
                              output=None)

    def adm_misc_check_rights(self):
        # ====================================================================
        'set access rights on fileserver'
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
            )
        for root, dirs, files in os.walk(tree, onerror=lambda x: self.ui.error(str(x))):
            for d in copy.copy(dirs):
                if os.path.join(root, d) in dir_exceptions:
                    dirs.remove(d)
            for f in copy.copy(files):
                if os.path.join(root, f) in file_exceptions:
                    files.remove(f)
            if not self.ui.args.dry_run:
                for d in dirs:
                    self.set_rights_dir(os.path.join(root, d), owner, group, dirmask)
                for f in files:
                    self.set_rights_file(os.path.join(root, f), owner, group, filemask)

    def media_pdf(self):
        # ====================================================================
        'embed OCR text in scanned PDF file'
        #
        #if not opts:
        #    raise self.exc_class('give a pdf filename via -o switch')
        #pdf_file = opts
        #pdf_base = os.path.splitext(pdf_file)[0]
        #
        #self.ui.ext_info('Extracting pages from scanned PDF...')
        #self.dispatch('pdfimages {0} {1}'.format(pdf_file, pdf_base),
        #              output='stderr')
        #
        ## determine list of extracted image files
        ## FIXME check if extracted images are in A4 format (pdfimages does not care if a pdf was scanned or not, this may lead to extracted logo images and such stuff)
        ## FIXME convert to TIF file directly using the -tiff switch of pdfimages
        #import glob
        #images = glob.iglob('{0}-[0-9]*.ppm'.format(pdf_base))
        #
        #for image in images:
        #    ppm_base = os.path.splitext(image)[0]
        #    tif_file = ppm_base + '.tif'
        #
        #    self.ui.ext_info('Apply threshold to {0}...'.format(ppm_base))
        #    # FIXME find optimal threshold value for OCR (test with
        #    # multiple pdfs)
        #    # FIXME what is this threshold value? color space?
        #    self.dispatch('convert {0} -threshold 50000 {1}'.format(image, tif_file),
        #                  output='stderr')
        #
        #    self.dispatch('TESSDATA_PREFIX=/usr/share/ tesseract {0} {1} -l deu'.format(tif_file, ppm_base),
        #                  output='stderr')
        #
        #    # FIXME
        #    # embed media text into pdf file
        #

    def adm_misc_check_audio(self):
        # ====================================================================
        'check audio metadata (low bitrates, ...)'
        walk = self.ui.args.options
        if not walk:
            walk = '/mnt/audio'

        # FIXME ignore single low bitrate if other songs in album are
        # okay, or simply ignore low bitrate VBR files

        lossy_extensions = re.compile(r'\.mp3$|\.ogg$', re.IGNORECASE)

        dir_exceptions = (
            '/mnt/audio/0_sort',
            )
        file_exceptions = (
            )
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            for d in copy.copy(dirs):
                if os.path.join(root, d) in dir_exceptions:
                    dirs.remove(d)
            for f in copy.copy(files):
                if os.path.join(root, f) in file_exceptions:
                    files.remove(f)
            for f in files:
                name = os.path.join(root, f)
                if lossy_extensions.search(name):
                    out = self.dispatch('exiftool "{0}" | grep -ai "Audio Bitrate\\|Nominal Bitrate"'.format(name),
                                        output=None).stdout[0]
                    # ignore unit specification
                    try:
                        bitrate = float(out.split()[-2])
                        if bitrate < 130:
                            self.ui.warning('Low audio bitrate detected: {1} ({0:-6f})'.format(bitrate, name))
                    except Exception:
                        self.ui.error('Bitrate extraction failed for: {0}'.format(name))

    def adm_misc_check_images(self):
        # ====================================================================
        'check image metadata (silently convert to xmp)'

        # FIXME
        self.ui.warning('DISABLED until properly implemented!')
        return

        walk = self.ui.args.options
        if not walk:
            walk = '/mnt/images'

        # - convert existing metadata to xmp, while deleting all
        #   metadata which cannot be converted to xmp.
        # - repair broken metadata structures
        self.dispatch('exiftool -q -r -P -overwrite_original -all= "-all>xmp:all" "{0}"'.format(walk))

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
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            for d in copy.copy(dirs):
                if os.path.join(root, d) in dir_exceptions:
                    dirs.remove(d)
            for d in dirs:
                dir_from_album_root = os.path.join(root, d).replace(walk, '').strip('/')
                dir_wo_metachars = dir_from_album_root.replace('/', '_').replace(' ', '_')
                self.dispatch('exiftool -q -P "-FileName<CreateDate" -d "{0}_%%Y-%%m-%%d_%%H-%%M-%%S%%%%-c.%%%%e" "{1}"'.format(dir_wo_metachars, os.path.join(root, d)))
            # check for missing CreateDate tag
            for f in files:
                if len(self.dispatch('exiftool -CreateDate "{0}"'.format(os.path.join(root, f)),
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


    def adm_misc_check_work(self):
        # ====================================================================
        'check data consistency on work'
        walk = self.ui.args.options
        if not walk:
            walk = '/mnt/work'
        sidecar_pdf_expected = re.compile(r'\.doc$|\.nb$|\.ppt$|\.vsd$|\.xls$', re.IGNORECASE)
        sidecar_pdf_wo_extension_expected = re.compile(r'exercise.*\.tex$', re.IGNORECASE)
        dir_exceptions = (
            '/mnt/work/0_sort',
            '/mnt/work/projects/backup',
            '/mnt/work/docs/education/thesis/competition',
            )
        file_exceptions = (
            )
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
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

    def adm_misc_check_filetypes(self):
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
                '/mnt/work/projects/backup',
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

    def adm_misc_check_filenames(self):
        # ====================================================================
        'check for names incompatible with other filesystems'
        walk = self.ui.args.options
        if not walk:
            walk = '/mnt'
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
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
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

if __name__ == '__main__':
    app = adm_misc(job_class=gentoo.job.job,
                   ui_class=ui)
    app.run()

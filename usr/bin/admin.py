#!/usr/bin/env python3
import collections
import errno
import itertools
import json
import multiprocessing
import os
import pylon.base as base
import pylon.gentoo.job as job
import pylon.gentoo.ui as ui
import re
import stat
import time

class ui(ui.ui):
    def __init__(self, owner):
        super().__init__(owner)
        self.parser_common.add_argument('-o', '--options', help='pass custom string to operations')
        self.parser_common.add_argument('-f', '--force', action='store_true')
         
        self.init_op_parser()
        self.parser_kernel.add_argument('--no-backup', action='store_true', help='do not overwrite keyring content with backup')
        self.parser_kernel.add_argument('-s', '--small', action='store_true', help='skip rsync of large ISOs')
         
        self.parser_check_repos.add_argument('-l', '--list_files', action='store_true')
        self.parser_sync.add_argument('-t', '--tree', action='store_true')
        self.parser_update.add_argument('-t', '--tree', action='store_true')

    def setup(self):
        super().setup()
        if not self.args.op:
            raise self.owner.exc_class('Specify at least one subcommand operation')
        
class admin(base.base):
    'script collection for system administration'

    def run_core(self):
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()

    @ui.log_exec_time
    def admin_check_audio(self):
        # ====================================================================
        'check audio metadata (low bitrates, ...)'
        walk = self.ui.args.options
        if not walk:
            walk = '/mnt/audio'

        dir_exceptions = (
            '0_sort',
            'ringtones'
            )
        file_exceptions = (
            '.stfolder',
            '.stignore',
        )

        media_files = list()
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            for d in list(dirs):
                if list(filter(lambda x: re.search(x, os.path.join(root, d)), dir_exceptions)):
                    dirs.remove(d)
            for f in list(files):
                if list(filter(lambda x: re.search(x, os.path.join(root, f)), file_exceptions)):
                    files.remove(f)
            media_files.extend(map(lambda x: os.path.join(root, x), files))
            if not dirs:
                if 'cover.jpg' not in files:
                    self.ui.warning('No album cover detected: {0}'.format(root))

        # process file list in chunks to avoid: Argument list is too long
        chunk_size = 1000
        for chunk in [media_files[x:x+chunk_size] for x in range(0, len(media_files), chunk_size)]:
            out = self.dispatch('exiftool -j "{0}"'.format('" "'.join(chunk)),
                                output=None).stdout
            for file_dict in json.loads(os.linesep.join(out)):
                filetype = file_dict['FileType']
                file     = file_dict['SourceFile']
                
                if filetype == 'MP3' or filetype == 'OGG':
                    try:
                        bitrate = file_dict['AudioBitrate']
                    except KeyError:
                        bitrate = file_dict['NominalBitrate']
                    # ignore unit specification
                    bitrate = float(bitrate.split()[-2])
                    if bitrate < 130:
                        self.ui.warning('Low audio bitrate detected: {1} ({0:-6f})'.format(bitrate, file))

                elif filetype == 'JPEG':
                    x,y = file_dict['ImageSize'].split('x')
                    if int(x) < 350 or int(y) < 350:
                        self.ui.warning('Low resolution (< 350x350) cover detected: {0}'.format(file))

    @ui.log_exec_time
    def admin_check_btrfs(self):
        # ====================================================================
        'periodic manual btrfs maintenance'

        btrfs_label = {
            'diablo': (
                'cache',
                'extpool',
                'pool',
            ),
            'belial': (
                'belial',
            ),
        }
        btrfs_mp_map = dict()

        for label in btrfs_label[self.ui.hostname]:
            if not self.ui.args.options or label in self.ui.args.options.split(','):
                # map label to device
                device = self.dispatch('/sbin/findfs LABEL={0}'.format(label),
                                       output=None, passive=True).stdout[0]
            
                # map device to first mountpoint
                out = self.dispatch('cat /proc/mounts | grep ' + device,
                                    output=None, passive=True).stdout[0]
                dev,mp,*rest = out.split(' ')

                btrfs_mp_map[label] = mp;

        def job(label, mp):
            self.ui.info('Scrubbing {0}...'.format(label))
            self.dispatch('/sbin/btrfs scrub start -BR -c 3 ' + mp)
            
            self.ui.info('Balancing metadata + data chunks for {0}...'.format(label))
            self.dispatch('/sbin/btrfs balance start -v -musage=50 -dusage=50 ' + mp)
            
            self.ui.info('Final usage stats for {0}...'.format(label))
            self.dispatch('/sbin/btrfs fi usage ' + mp,
                          passive=True)
            
        for label,mp in btrfs_mp_map.items():
            self.dispatch(lambda label=label,mp=mp: job(label,mp),
                          blocking=False)
        self.join()

        # - FIXME perform periodic defrag after "snapshot-aware defragmentation" is available again
        # - FIXME some paths are not escaped correctly
        btrfs_filefrag_roots = {
            'diablo': (
                '/home',
                #'/',
                #'/mnt/games',
                #'/mnt/video',
            ),
            'belial': (
                '/',
            ),
        }
        extent_pattern = re.compile(' [0-9]{3,} extent')
        #for r in btrfs_filefrag_roots[self.ui.hostname]:
        #    for root, dirs, files in os.walk(r, onerror=lambda x: self.ui.error(str(x))):
        #        for d in list(dirs):
        #            path = os.path.join(root, d)
        #            if os.path.ismount(path):
        #                dirs.remove(d)
        #        for f in files:
        #            path = os.path.join(root, f).replace('$','\$')
        #            try:
        #                output = self.dispatch('/usr/sbin/filefrag "{0}"'.format(path),
        #                                       output=None).stdout
        #                if output:
        #                    match = extent_pattern.search(output[0])
        #                    if match:
        #                        self.ui.warning(match.string)
        #            except self.exc_class:
        #                self.ui.error('filefrag failed for {0}'.format(path))

    @ui.log_exec_time
    def admin_check_docs(self):
        # ====================================================================
        'check data consistency on docs'

        # FIXME - check for invalid files in 0_blacklist folder (maybe just allow /mnt/docs/0_sort in check_filetypes)

        # FIXME ensure Octave compatibility:
        # find /mnt/docs/0_sort/ -type f | grep '.*\.m$'
        # /mnt/docs/0_archive/WaveProcessor/matlab-sim/*
        # /mnt/docs/0_archive/fpcore/units/fpcoreblks/source/matlab/fpcoreblks/fpcoreblks/slblocks.m
        # /mnt/docs/systems/communication/hsse00_nat_exercises
        # /mnt/docs/systems/dsp/fir iir/example_for_iir_analysis.m
        # /mnt/docs/systems/control/hsse00_ret_exercises
        # /mnt/docs/systems/dsp/hsse00_set5_exercises
        # /mnt/docs/systems/dsp/hsse00_syt4_exercises
        # /mnt/docs/systems/dsp/hsse01_syt4_exercises
        # /mnt/docs/systems/dsp/noiseshaping
        # ??? mnt/docs/systems/modeling/matlab/MSystem
        # ??? mnt/docs/systems/modeling/matlab/mdt
        
        
        walk = self.ui.args.options
        if not walk:
            walk = '/mnt/docs'
        sidecar_pdf_expected = re.compile(r'\.doc$|\.nb$|\.ppt$|\.vsd$|\.xls$', re.IGNORECASE)
        sidecar_pdf_wo_extension_expected = re.compile(r'exercise.*\.tex$', re.IGNORECASE)
        dir_exceptions = (
            '/mnt/docs/education/thesis/competition',
            )
        file_exceptions = (
            )
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            for d in list(dirs):
                if os.path.join(root, d) in dir_exceptions:
                    dirs.remove(d)
            for f in list(files):
                if os.path.join(root, f) in file_exceptions:
                    files.remove(f)
            for f in files:
                sidecar = f + '.pdf'
                sidecar_wo_extension = os.path.splitext(f)[0] + '.pdf'
                if (sidecar_pdf_expected.search(f) and not sidecar in files or
                    sidecar_pdf_wo_extension_expected.search(f) and not sidecar_wo_extension in files):
                    self.ui.warning('Sidecar PDF expected for: ' + os.path.join(root, f))
        #'embed OCR text in scanned PDF file'
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

    @ui.log_exec_time
    def admin_check_filenames(self):
        # ====================================================================
        'check for names incompatible with other filesystems'
        walk = self.ui.args.options
        if not walk:
            walk = '/mnt'

        # os & os.path & pathlib DO NOT provide anything to check NT path validity
        # custom implementation inspired from
        # - https://stackoverflow.com/questions/62771/how-do-i-check-if-a-given-string-is-a-legal-valid-file-name-under-windows
        # - https://github.com/markwingerd/checkdir/blob/master/checkdir.py
        #   * < > : " / \ | ? *
        #   * Characters whose integer representations are 0-31 (less than ASCII space)
        #   * Any other character that the target file system does not allow (say, trailing periods or spaces)
        #   * Any of the DOS names: CON, PRN, AUX, NUL, COM1, COM2, COM3, COM4, COM5, COM6, COM7, COM8, COM9, LPT1, LPT2, LPT3, LPT4, LPT5, LPT6, LPT7, LPT8, LPT9 (and avoid AUX.txt, etc)
        #   * The file name is all periods
        #   * File paths (including the file name) may not have more than 260 characters (that don't use the \?\ prefix)
        #   * Unicode file paths (including the file name) with more than 32,000 characters when using \?\ (note that prefix may expand directory components and cause it to overflow the 32,000 limit)
        ntfs_invalid_names = re.compile(r'^(PRN|AUX|NUL|CON|COM[1-9]|LPT[1-9])(\..*)?$')
        ntfs_invalid_chars = re.compile(r'[\"*:<>?/|]')
        # . as first character can be valid, see https://stackoverflow.com/questions/10744305/how-to-create-gitignore-file
        ntfs_invalid_trailing_chars = re.compile(r'\.$|^\ |\ $')
            
        dir_exceptions = (
            '/mnt/work/backup',
            )
        file_exceptions = (
            )
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            for d in list(dirs):
                if list(filter(lambda x: re.search(x, os.path.join(root, d)), dir_exceptions)):
                    dirs.remove(d)
            for f in list(files):
                if list(filter(lambda x: re.search(x, os.path.join(root, f)), file_exceptions)):
                    files.remove(f)
            names = list(dirs)
            names.extend(files)
            lower_case_dupe_map = collections.Counter([x.lower() for x in names])
            for name in sorted(names):
                if (ntfs_invalid_names.search(name) or
                    ntfs_invalid_chars.search(name) or
                    ntfs_invalid_trailing_chars.search(name) or
                    len(name) > 255 or
                    list(filter(lambda c: ord(c) < 32, name))):
                    self.ui.warning('NTFS incompatible filesystem object: ' + os.path.join(root, name))
                if lower_case_dupe_map[name.lower()] > 1:
                    self.ui.warning('Filesystem objects only distinguished by case: ' + os.path.join(root, name))

    @ui.log_exec_time
    def admin_check_filetypes(self):
        # ====================================================================
        'check for expected/unexpected filetypes on fileserver'

        # match nothing for now
        allowed_global = re.compile('a^')
        allowed = {
            'audio': re.compile('\.flac$|\.mp3$|\.ogg$|cover\.jpg$'),
            # FIXME remove 0_sort after major filetype cleanup
            'docs': re.compile('\.epub$|\.jpg$|\.opf$|\.pdf$|\.tex$|/0_sort/.*'),
            # FIXME check for any files in games 0_blacklist folder
            'games': re.compile('.*'),
            # FIXME
            # - rename *.JPG to .jpg to see files also with restrictive filter dialogs
            # - remove 0_sort after major filetype cleanup
            'images': re.compile('\.' + '$|\.'.join([
                # uncompressed
                'gif','png',
                # compressed
                'jpg',
                # camera videos
                'avi',
            ]) + '$' + '|/0_sort/.*', re.IGNORECASE),
            # FIXME check for any files in video 0_blacklist folder
            'video': re.compile('\.' + '$|\.'.join([
                # metadata
                'jpg','nfo',
                # subtitles
                'idx','srt','sub',
                # bluray/dvd files
                'bup','ifo','img','iso','m2ts','vob',
                # video container
                'avi','flv','mkv','mp4','mpg','ogm',
            ]) + '$', re.IGNORECASE),
            'work': re.compile('.*'),
            }

        # FIXME search all folder for syncthings tmp files: ~Past.pst.tmp
        forbidden_global = re.compile('sync-conflict|/~[^~]*\.tmp$', re.IGNORECASE)
        forbidden = {
            'audio': re.compile('a^'),
            'docs': re.compile('a^'),
            'games': re.compile('a^'),
            'images': re.compile('a^'),
            'video': re.compile('a^'),
            'work': re.compile('a^'),
            }
        
        dir_exceptions = (
            '/mnt/work/backup',
            '/mnt/docs/education/thesis/competition/', #FIXME
            )
        file_exceptions = (
            '/mnt/audio/comedy/.stfolder',
            '/mnt/audio/downtempo/.stfolder',
            '/mnt/audio/electronic/.stfolder',
            '/mnt/audio/metal/.stfolder',
            '/mnt/audio/ringtones/.stfolder',
            '/mnt/docs/.stfolder',
            '/mnt/docs/.stignore',
            '/mnt/images/.stfolder',
            '/mnt/images/.stignore',
            '/mnt/images/0_sort/company/.stfolder',
            '/mnt/images/0_sort/hannes/.stfolder',
            '/mnt/work/backup/outlook/.stfolder',
            '/mnt/work/backup/titanium.hannes/.stfolder',
        )
        for k in allowed.keys():
            for root, dirs, files in os.walk(os.path.join('/mnt', k), onerror=lambda x: self.ui.error(str(x))):
                for d in list(dirs):
                    if list(filter(lambda x: re.search(x, os.path.join(root, d)), dir_exceptions)):
                        dirs.remove(d)
                for f in list(files):
                    if list(filter(lambda x: re.search(x, os.path.join(root, f)), file_exceptions)):
                        files.remove(f)
                for f in files:
                    name = os.path.join(root, f)
                    if ((not allowed_global.search(name) and
                         not allowed[k].search(name)) or
                        forbidden_global.search(name) or
                        forbidden[k].search(name)):
                        self.ui.warning('Unexpected filetype detected: ' + name)
                        
    @ui.log_exec_time
    def admin_check_images(self):
        # ====================================================================
        'check image metadata (silently convert to xmp)'

        # FIXME
        # - consistently apply date & geotag metadata => best search possibilites (or remove gps data to allow easy publication)
        #   - delete geotagging metadata? identify incriminating XMP metadata and remove?
        # - exiftool file renaming
        #   - do I still need this command?: exiftool -r -P '-FileName<ModifyDate' -d %Y-%m-%d_%H-%M-%S%%-c.%%e <file>
        #   - report wrong file name format?
        #   - usecase for automatic renaming flow: /mnt/images/pets/stanz/IMG_0006.JPG
        # - rotation
        #   - automatic rotation by exiftool? rotated in other tools, eg pets_stanz_2010-09-24_15-43-59.JPG
        #   - report wrong rotation in xmp metadata
        # - rating supported by xmp & digikam?
        #
        # MANUAL FIX QUICK REFERENCE
        # - add xmp standard caption for digikam viewing
        #   exiftool -P -overwrite_original -xmp:Description="caption for digikam" <file>
        # - change creation date
        #   exiftool -P -overwrite_original -xmp:CreateDate="1990:02:01 00:00:00" <file>
        # - check for any existing EXIF / IPTC / XMP metadata
        #   exiftool -a -G1 * | grep -v ExifTool | grep -v System | grep -v File | grep -v Composite | grep -v PNG | grep -v =======
        #
        # LAST BAAL REPORT
        ### adm_misc(2014-03-04 21:58:32,261) WARNING: Missing CreateDate tag for: /mnt/images/lindner/simone/simone4.jpg
        ### adm_misc(2014-03-04 21:58:32,780) WARNING: Missing CreateDate tag for: /mnt/images/lindner/simone/italien8.jpg
        ### adm_misc(2014-03-04 21:58:35,002) WARNING: Missing CreateDate tag for: /mnt/images/lindner/simone/simone2.jpg
        ### adm_misc(2014-03-04 21:59:34,790) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/anna1.jpg
        ### adm_misc(2014-03-04 21:59:35,584) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/italien9.jpg
        ### adm_misc(2014-03-04 21:59:36,197) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/anna6.jpg
        ### adm_misc(2014-03-04 21:59:38,030) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/anna8.jpg
        ### adm_misc(2014-03-04 21:59:45,940) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/anna9.jpg
        ### adm_misc(2014-03-04 21:59:48,680) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/anna4.jpg
        ### adm_misc(2014-03-04 21:59:51,295) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/anna3.jpg
        ### adm_misc(2014-03-04 21:59:53,939) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/anna2.jpg
        ### adm_misc(2014-03-04 21:59:59,167) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/anna7.jpg
        ### adm_misc(2014-03-04 22:00:04,550) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/anna5.jpg
        ### adm_misc(2014-03-04 22:00:09,937) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/italien2.jpg
        ### adm_misc(2014-03-04 22:00:20,463) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/matura/DSCF1302.JPG
        ### adm_misc(2014-03-04 22:00:23,070) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/matura/DSCF1311.JPG
        ### adm_misc(2014-03-04 22:00:25,473) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/matura/DSCF1298.JPG
        ### adm_misc(2014-03-04 22:00:33,522) WARNING: Missing CreateDate tag for: /mnt/images/lindner/anna/matura/DSCF1294.JPG
        ### adm_misc(2014-03-04 22:13:48,287) WARNING: Missing CreateDate tag for: /mnt/images/trips/2006.09 totes gebirge/stÃ¶gi/eTag1_Hinterstoder_Prielschutzhaus.jpg
        ### adm_misc(2014-03-04 22:13:53,006) WARNING: Missing CreateDate tag for: /mnt/images/trips/2006.09 totes gebirge/stÃ¶gi/eTag2_Prielschutzhaus_PÃ¼hringerhÃ¼tte.jpg
        ### adm_misc(2014-03-04 22:14:58,860) WARNING: Missing CreateDate tag for: /mnt/images/trips/2006.09 totes gebirge/stÃ¶gi/eTag4_Appelhaus_LoserhÃ¼tte.jpg
        ### adm_misc(2014-03-04 22:15:01,396) WARNING: Missing CreateDate tag for: /mnt/images/trips/2006.09 totes gebirge/stÃ¶gi/Karte2_small.jpg
        ### adm_misc(2014-03-04 22:15:46,621) WARNING: Missing CreateDate tag for: /mnt/images/trips/2006.09 totes gebirge/stÃ¶gi/eTag5_LoserhÃ¼tte_Tauplitzalm.jpg
        ### adm_misc(2014-03-04 22:15:55,202) WARNING: Missing CreateDate tag for: /mnt/images/trips/2006.09 totes gebirge/stÃ¶gi/eTag3_PÃ¼hringerhÃ¼tte_Appelhaus.jpg
        ### adm_misc(2014-03-04 22:15:58,156) WARNING: Missing CreateDate tag for: /mnt/images/trips/2006.09 totes gebirge/stÃ¶gi/eTag6_Tauplitz_Hinterstoder.jpg
        ### adm_misc(2014-03-04 22:21:42,672) WARNING: Missing CreateDate tag for: /mnt/images/trips/2006.09 totes gebirge/deix/060909-TotesGeb_16-p.jpg
        ### adm_misc(2014-03-04 22:41:03,411) WARNING: Missing CreateDate tag for: /mnt/images/events/2010 mitterecker/DSCF0331.AVI
        ### adm_misc(2014-03-04 22:41:54,198) WARNING: Missing CreateDate tag for: /mnt/images/events/2010 traktortreffen/DSCF0151.AVI
        ### adm_misc(2014-03-04 22:43:47,196) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-07-33.jpg
        ### adm_misc(2014-03-04 22:43:49,343) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-05-34.jpg
        ### adm_misc(2014-03-04 22:43:51,796) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-09-12.jpg
        ### adm_misc(2014-03-04 22:43:54,370) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-10-27.jpg
        ### adm_misc(2014-03-04 22:43:57,158) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-06-25.jpg
        ### adm_misc(2014-03-04 22:43:59,752) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-07-22.jpg
        ### adm_misc(2014-03-04 22:44:02,403) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-08-51.jpg
        ### adm_misc(2014-03-04 22:44:04,646) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-13-04.jpg
        ### adm_misc(2014-03-04 22:44:07,304) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-07-05.jpg
        ### adm_misc(2014-03-04 22:44:09,843) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-07-49.jpg
        ### adm_misc(2014-03-04 22:44:12,495) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-11-31.jpg
        ### adm_misc(2014-03-04 22:44:15,024) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-09-35.jpg
        ### adm_misc(2014-03-04 22:44:17,717) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-12-45.jpg
        ### adm_misc(2014-03-04 22:44:20,433) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-20-26.jpg
        ### adm_misc(2014-03-04 22:44:22,967) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-13-17.jpg
        ### adm_misc(2014-03-04 22:44:25,539) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-04-54.jpg
        ### adm_misc(2014-03-04 22:44:27,606) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-11-21.jpg
        ### adm_misc(2014-03-04 22:44:29,980) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-12-23.jpg
        ### adm_misc(2014-03-04 22:44:32,654) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-09-23.jpg
        ### adm_misc(2014-03-04 22:44:35,205) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-12-12.jpg
        ### adm_misc(2014-03-04 22:44:37,646) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-11-42.jpg
        ### adm_misc(2014-03-04 22:44:40,285) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-06-48.jpg
        ### adm_misc(2014-03-04 22:44:42,885) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-08-33.jpg
        ### adm_misc(2014-03-04 22:44:45,421) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-11-59.jpg
        ### adm_misc(2014-03-04 22:44:47,413) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-08-15.jpg
        ### adm_misc(2014-03-04 22:44:50,099) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-10-04.jpg
        ### adm_misc(2014-03-04 22:44:52,434) WARNING: Missing CreateDate tag for: /mnt/images/events/2004 christmas/2004-12-26_15-12-31.jpg
        ### adm_misc(2014-03-04 22:51:39,509) WARNING: Missing CreateDate tag for: /mnt/images/paul/Paul's Vernisage, Kunst & Krempel.mpg
        ### adm_misc(2014-03-04 22:51:40,012) WARNING: Missing CreateDate tag for: /mnt/images/paul/paul2.jpg
        ### adm_misc(2014-03-04 22:51:52,780) WARNING: Missing CreateDate tag for: /mnt/images/paul/paul6.jpg
        ### adm_misc(2014-03-04 22:51:52,972) WARNING: Missing CreateDate tag for: /mnt/images/paul/valentinstag2.jpg
        ### adm_misc(2014-03-04 22:51:53,162) WARNING: Missing CreateDate tag for: /mnt/images/paul/paul.jpg
        ### adm_misc(2014-03-04 22:51:54,823) WARNING: Missing CreateDate tag for: /mnt/images/paul/paul3.jpg
        ### adm_misc(2014-03-04 22:51:56,563) WARNING: Missing CreateDate tag for: /mnt/images/paul/paul4.jpg
        ### adm_misc(2014-03-04 22:51:58,396) WARNING: Missing CreateDate tag for: /mnt/images/paul/paul5.jpg
        ### adm_misc(2014-03-04 22:52:01,191) WARNING: Missing CreateDate tag for: /mnt/images/paul/valentinstag1.jpg
        ### adm_misc(2014-03-04 22:52:01,389) WARNING: Missing CreateDate tag for: /mnt/images/paul/silvester8.jpg
        ### adm_misc(2014-03-04 22:52:03,250) WARNING: Missing CreateDate tag for: /mnt/images/paul/paul 13x18x2.jpg
        ### adm_misc(2014-03-04 22:52:03,482) WARNING: Missing CreateDate tag for: /mnt/images/paul/paul1.jpg
        ### adm_misc(2014-03-04 22:52:04,508) WARNING: Missing CreateDate tag for: /mnt/images/paul/paul7.jpg
        ### adm_misc(2014-03-04 22:52:05,390) WARNING: Missing CreateDate tag for: /mnt/images/paul/paul22.jpg
        ### adm_misc(2014-03-04 22:52:11,432) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald1.jpg
        ### adm_misc(2014-03-04 22:52:12,119) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald8.jpg
        ### adm_misc(2014-03-04 22:52:12,316) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald2.jpg
        ### adm_misc(2014-03-04 22:52:12,508) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald14.jpg
        ### adm_misc(2014-03-04 22:52:13,905) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/platz4 13x18.jpg
        ### adm_misc(2014-03-04 22:52:14,300) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald7.jpg
        ### adm_misc(2014-03-04 22:52:15,111) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald6.jpg
        ### adm_misc(2014-03-04 22:52:17,187) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald12.jpg
        ### adm_misc(2014-03-04 22:52:24,172) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald10.jpg
        ### adm_misc(2014-03-04 22:52:24,361) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/platz1 13x18.jpg
        ### adm_misc(2014-03-04 22:52:24,561) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald11.jpg
        ### adm_misc(2014-03-04 22:52:24,761) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/platz5 13x18.jpg
        ### adm_misc(2014-03-04 22:52:25,155) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald4.jpg
        ### adm_misc(2014-03-04 22:52:25,551) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald3.jpg
        ### adm_misc(2014-03-04 22:52:35,268) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/tor1 13x18.jpg
        ### adm_misc(2014-03-04 22:52:37,954) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald16.jpg
        ### adm_misc(2014-03-04 22:52:40,259) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald18.jpg
        ### adm_misc(2014-03-04 22:52:40,450) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/platz3 13x18.jpg
        ### adm_misc(2014-03-04 22:52:40,658) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald17.jpg
        ### adm_misc(2014-03-04 22:52:41,454) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald15.jpg
        ### adm_misc(2014-03-04 22:52:41,859) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/platz2 13x18.jpg
        ### adm_misc(2014-03-04 22:52:42,550) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald5.jpg
        ### adm_misc(2014-03-04 22:52:45,280) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald13.jpg
        ### adm_misc(2014-03-04 22:52:47,880) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/tor2 13x18.jpg
        ### adm_misc(2014-03-04 22:52:56,635) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/wald9.jpg
        ### adm_misc(2014-03-04 22:53:19,469) WARNING: Missing CreateDate tag for: /mnt/images/paul/wald/approved/platz3 13x18.jpg
        ### adm_misc(2014-03-04 22:53:39,311) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/DSCF0963.JPG
        ### adm_misc(2014-03-04 22:53:54,605) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/DSCF1205.JPG
        ### adm_misc(2014-03-04 22:53:59,380) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/DSCF1256.JPG
        ### adm_misc(2014-03-04 22:54:11,363) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/DSCF1240.JPG
        ### adm_misc(2014-03-04 22:54:13,596) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/DSCF1044.JPG
        ### adm_misc(2014-03-04 22:54:42,012) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/DSCF1125.JPG
        ### adm_misc(2014-03-04 22:55:18,252) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/DSCF0991.JPG
        ### adm_misc(2014-03-04 22:55:22,606) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/DSCF1139.JPG
        ### adm_misc(2014-03-04 22:55:23,867) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1165.AVI
        ### adm_misc(2014-03-04 22:55:24,249) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1064.AVI
        ### adm_misc(2014-03-04 22:55:24,511) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1177.AVI
        ### adm_misc(2014-03-04 22:55:24,781) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1081.AVI
        ### adm_misc(2014-03-04 22:55:25,039) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1141.AVI
        ### adm_misc(2014-03-04 22:55:25,287) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1111.AVI
        ### adm_misc(2014-03-04 22:55:25,502) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1168.AVI
        ### adm_misc(2014-03-04 22:55:25,820) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1073.AVI
        ### adm_misc(2014-03-04 22:55:26,076) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1231.AVI
        ### adm_misc(2014-03-04 22:55:26,347) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1116.AVI
        ### adm_misc(2014-03-04 22:55:26,616) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1192.AVI
        ### adm_misc(2014-03-04 22:55:26,876) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1148.AVI
        ### adm_misc(2014-03-04 22:55:27,192) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1069.AVI
        ### adm_misc(2014-03-04 22:55:27,469) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1109.AVI
        ### adm_misc(2014-03-04 22:55:27,736) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1066.AVI
        ### adm_misc(2014-03-04 22:55:28,026) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1172.AVI
        ### adm_misc(2014-03-04 22:55:28,261) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1175.AVI
        ### adm_misc(2014-03-04 22:55:28,490) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1174.AVI
        ### adm_misc(2014-03-04 22:55:28,731) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1095.AVI
        ### adm_misc(2014-03-04 22:55:29,038) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1179.AVI
        ### adm_misc(2014-03-04 22:55:29,265) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1147.AVI
        ### adm_misc(2014-03-04 22:55:29,534) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1170.AVI
        ### adm_misc(2014-03-04 22:55:29,768) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1222.AVI
        ### adm_misc(2014-03-04 22:55:30,056) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1071.AVI
        ### adm_misc(2014-03-04 22:55:30,290) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1128.AVI
        ### adm_misc(2014-03-04 22:55:30,528) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1074.AVI
        ### adm_misc(2014-03-04 22:55:30,838) WARNING: Missing CreateDate tag for: /mnt/images/paul/2012.06 england/video/DSCF1134.AVI
        ### adm_misc(2014-03-04 22:57:22,332) WARNING: Missing CreateDate tag for: /mnt/images/hannes/erstkommunion/Kommunion Hannes.mpg
        ### adm_misc(2014-03-04 23:18:59,730) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_461.jpg
        ### adm_misc(2014-03-04 23:19:02,373) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_439.jpg
        ### adm_misc(2014-03-04 23:19:05,095) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/Krumau_Hauptplatz.jpg
        ### adm_misc(2014-03-04 23:19:07,813) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/Krumau_Blick_von_der_Burg_2.jpg
        ### adm_misc(2014-03-04 23:19:10,432) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_463.jpg
        ### adm_misc(2014-03-04 23:19:12,964) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_426.jpg
        ### adm_misc(2014-03-04 23:19:15,793) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_540.jpg
        ### adm_misc(2014-03-04 23:19:18,386) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_434.jpg
        ### adm_misc(2014-03-04 23:19:20,910) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_484.jpg
        ### adm_misc(2014-03-04 23:19:23,733) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_436.jpg
        ### adm_misc(2014-03-04 23:19:26,227) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_407.jpg
        ### adm_misc(2014-03-04 23:19:28,881) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_590.jpg
        ### adm_misc(2014-03-04 23:19:31,603) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_480.jpg
        ### adm_misc(2014-03-04 23:19:34,259) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/Krumau_Burg.jpg
        ### adm_misc(2014-03-04 23:19:36,984) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/Krumau_GrandHotel.jpg
        ### adm_misc(2014-03-04 23:19:39,394) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_416.jpg
        ### adm_misc(2014-03-04 23:19:42,175) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_498.jpg
        ### adm_misc(2014-03-04 23:19:45,005) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/Krumau_Blick_von_der_Burg_1.jpg
        ### adm_misc(2014-03-04 23:19:47,645) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_406.jpg
        ### adm_misc(2014-03-04 23:19:50,214) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_603.jpg
        ### adm_misc(2014-03-04 23:19:53,075) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_490.jpg
        ### adm_misc(2014-03-04 23:19:55,704) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_443.jpg
        ### adm_misc(2014-03-04 23:19:58,283) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_444.jpg
        ### adm_misc(2014-03-04 23:20:01,146) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/Krumau_Schlosshof.jpg
        ### adm_misc(2014-03-04 23:20:03,876) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_464.jpg
        ### adm_misc(2014-03-04 23:20:06,525) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_605.jpg
        ### adm_misc(2014-03-04 23:20:09,369) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/Krumau_2.jpg
        ### adm_misc(2014-03-04 23:20:12,153) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_596.jpg
        ### adm_misc(2014-03-04 23:20:14,666) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_548.jpg
        ### adm_misc(2014-03-04 23:20:17,198) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_542.jpg
        ### adm_misc(2014-03-04 23:20:20,080) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/DSC_6251_3D.JPG
        ### adm_misc(2014-03-04 23:20:22,839) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_501.jpg
        ### adm_misc(2014-03-04 23:20:25,432) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_593.jpg
        ### adm_misc(2014-03-04 23:20:28,036) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_532.jpg
        ### adm_misc(2014-03-04 23:20:30,690) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_462.jpg
        ### adm_misc(2014-03-04 23:20:33,392) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/Krumau_1.jpg
        ### adm_misc(2014-03-04 23:20:36,211) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_567.jpg
        ### adm_misc(2014-03-04 23:20:38,869) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_537.jpg
        ### adm_misc(2014-03-04 23:20:41,408) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_488.jpg
        ### adm_misc(2014-03-04 23:20:43,949) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_449.jpg
        ### adm_misc(2014-03-04 23:20:46,434) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_547.jpg
        ### adm_misc(2014-03-04 23:20:49,201) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_482.jpg
        ### adm_misc(2014-03-04 23:20:51,645) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_534.jpg
        ### adm_misc(2014-03-04 23:20:54,603) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_452.jpg
        ### adm_misc(2014-03-04 23:20:57,270) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_599.jpg
        ### adm_misc(2014-03-04 23:21:00,155) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_594.jpg
        ### adm_misc(2014-03-04 23:21:02,981) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_524.jpg
        ### adm_misc(2014-03-04 23:21:05,670) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_522.jpg
        ### adm_misc(2014-03-04 23:21:08,387) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_467.jpg
        ### adm_misc(2014-03-04 23:21:11,108) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_602.jpg
        ### adm_misc(2014-03-04 23:21:13,692) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_533.jpg
        ### adm_misc(2014-03-04 23:21:16,443) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_591.jpg
        ### adm_misc(2014-03-04 23:21:19,161) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_435.jpg
        ### adm_misc(2014-03-04 23:21:22,017) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080626_AP_425.jpg
        ### adm_misc(2014-03-04 23:21:24,903) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_601.jpg
        ### adm_misc(2014-03-04 23:21:27,665) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2008.06 company outing/Andreas Puerstinger/20080627_AP_544.jpg
        ### adm_misc(2014-03-04 23:34:48,008) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2005.09 company outing/Stitch.jpg
        ### adm_misc(2014-03-04 23:37:00,157) WARNING: Missing CreateDate tag for: /mnt/images/hannes/dice/2005.09 company outing/IMG_0350.jpg
        ### adm_misc(2014-03-04 23:45:16,656) WARNING: Missing CreateDate tag for: /mnt/images/hannes/school/maturaball/Matura Hannes.mpg
        ### adm_misc(2014-03-04 23:52:21,968) WARNING: Missing CreateDate tag for: /mnt/images/hannes/school/diplomreise/DSC00225_.JPG
        ### adm_misc(2014-03-05 00:00:56,192) WARNING: Missing CreateDate tag for: /mnt/images/helga/verwandtschaft/thomas/Matura Thomas.mpg
        ### adm_misc(2014-03-05 00:01:07,489) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/silvester9.jpg
        ### adm_misc(2014-03-05 00:01:07,714) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/traudi2.jpg
        ### adm_misc(2014-03-05 00:01:22,561) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/italien3.jpg
        ### adm_misc(2014-03-05 00:01:30,386) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/goisererhtte3.jpg
        ### adm_misc(2014-03-05 00:01:38,455) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/silvester10.jpg
        ### adm_misc(2014-03-05 00:01:40,946) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/snowboard2.jpg
        ### adm_misc(2014-03-05 00:01:59,426) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/goisererhtte2.jpg
        ### adm_misc(2014-03-05 00:02:07,472) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/goisererhtte1.jpg
        ### adm_misc(2014-03-05 00:02:33,812) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/snowboard1.jpg
        ### adm_misc(2014-03-05 00:02:36,900) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/italien6.jpg
        ### adm_misc(2014-03-05 00:02:39,213) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/helga 13x18x2.jpg
        ### adm_misc(2014-03-05 00:02:48,878) WARNING: Missing CreateDate tag for: /mnt/images/helga/helga/snowboard3.jpg
        ### adm_misc(2014-03-05 00:04:29,468) WARNING: Missing CreateDate tag for: /mnt/images/helga/birthdays/DSCF1330.JPG
        ### adm_misc(2014-03-05 00:10:44,198) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-02-14_002.jpg
        ### adm_misc(2014-03-05 00:10:53,696) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-02-14_016.jpg
        ### adm_misc(2014-03-05 00:11:08,649) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-01-22_008.jpg
        ### adm_misc(2014-03-05 00:11:13,934) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-02-14_014.jpg
        ### adm_misc(2014-03-05 00:11:16,396) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-01-22_007.jpg
        ### adm_misc(2014-03-05 00:11:27,038) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-02-14_013.jpg
        ### adm_misc(2014-03-05 00:11:40,605) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-01-22_006.jpg
        ### adm_misc(2014-03-05 00:12:17,134) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2003-01-04_009.jpg
        ### adm_misc(2014-03-05 00:12:24,856) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2003-01-04_001.jpg
        ### adm_misc(2014-03-05 00:12:38,012) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-02-14_004.jpg
        ### adm_misc(2014-03-05 00:12:58,895) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-02-14_015.jpg
        ### adm_misc(2014-03-05 00:13:22,249) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-02-14_003.jpg
        ### adm_misc(2014-03-05 00:13:48,517) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-12-26_010.jpg
        ### adm_misc(2014-03-05 00:13:57,345) WARNING: Missing CreateDate tag for: /mnt/images/pets/stanz/stanz_2004-01-24_005.jpg
        #self.ui.warning('DISABLED until properly implemented!')
        #return

        walk = self.ui.args.options
        if not walk:
            walk = '/mnt/images'
            
        # - convert existing metadata to xmp, while deleting all
        #   metadata which cannot be converted to xmp.
        # - repair broken metadata structures
        #self.dispatch('exiftool -q -r -P -overwrite_original -all= "-all>xmp:all" "{0}"'.format(walk))

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
            '/mnt/images/[a-s]',
            '/mnt/images/[u-z]',
            '/mnt/images/0_sort',
            #'/mnt/images/cuteness',
            #'/mnt/images/design',
            #'/mnt/images/fun',
        )
        file_exceptions = (
            '.stfolder',
            '.stignore',
        )
        def chunks(l, n):
            for i in xrange(0, len(l), n):
                yield l[i:i+n]

        metadata = dict()
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            for d in list(dirs):
                if list(filter(lambda x: re.search(x, os.path.join(root, d)), dir_exceptions)):
                    dirs.remove(d)
            combined_regex = re.compile('|'.join(file_exceptions))
            abs_paths = map(lambda x: os.path.join(root, x), files)
            excl_paths = filter(lambda x: not re.search(combined_regex, x), abs_paths)
            quoted_paths = ['"' + path + '"' for path in excl_paths]
            if quoted_paths:
                out = self.dispatch('exiftool -Orientation {0}'.format(' '.join(quoted_paths)),
                                    output=None).stdout
                orientation_tags = out[1::2]
                metadata.update(zip(excl_paths, orientation_tags))
        print(metadata)
            #while(chunks(excl_paths, 10)):
            #    out = self.dispatch('exiftool -Orientation {0}'.format(' '.join(excl_paths)),
            #                        output=None).stdout
            #        if len()out:
            #            self.ui.warning('No orientation flag: {0}'.format(path))
            #        elif 'normal' not in out[0]:
            #            self.ui.info('{0} {1}'.format(out, path))

            #for d in dirs:
            #    dir_from_album_root = os.path.join(root, d).replace(walk, '').strip('/')
            #    dir_wo_metachars = dir_from_album_root.replace('/', '_').replace(' ', '_')
            #    self.dispatch('exiftool -q -P "-FileName<CreateDate" -d "{0}_%%Y-%%m-%%d_%%H-%%M-%%S%%%%-c.%%%%e" "{1}"'.format(dir_wo_metachars, os.path.join(root, d)))
            ## check for missing CreateDate tag
            #for f in files:
            #    if len(self.dispatch('exiftool -CreateDate "{0}"'.format(os.path.join(root, f)),
            #                         None).stdout) == 0:
            #        self.ui.warning('Missing CreateDate tag for: ' + os.path.join(root, f))

    @ui.log_exec_time
    def admin_check_permissions(self):
        # ====================================================================
        'check and apply access rights on system & user data paths'

        def set_rights_dir(dir, owner, group, dirmask):
            os.chown(dir, owner, group)
            os.chmod(dir, dirmask)

        def set_rights_file(file, owner, group, filemask):
            os.chown(file, owner, group)
            os.chmod(file, filemask)

        def set_rights(tree,
                       owner=1000,  # schweizer
                       group=100,   # users
                       dirmask=0o750,
                       filemask=0o640):
            dir_exceptions = (
                '/mnt/work/backup/cache',
                '/mnt/work/backup/pool',
                '/mnt/work/firewall',
                '/mnt/work/software',
                '/mnt/work/usb_boot',
                '/mnt/work/webserver',
                )
            file_exceptions = (
                )
            for root, dirs, files in os.walk(tree, onerror=lambda x: self.ui.error(str(x))):
                for d in list(dirs):
                    if list(filter(lambda x: re.search(x, os.path.join(root, d)), dir_exceptions)):
                        dirs.remove(d)
                for f in list(files):
                    if list(filter(lambda x: re.search(x, os.path.join(root, f)), file_exceptions)):
                        files.remove(f)
                if not self.ui.args.dry_run:
                    for d in dirs:
                        set_rights_dir(os.path.join(root, d), owner, group, dirmask)
                    for f in files:
                        set_rights_file(os.path.join(root, f), owner, group, filemask)
        
        public = (
            '/mnt/audio',
            '/mnt/games',
            '/mnt/video',
            )
        self.ui.info('Setting rights for public data...')
        for p in public:
            self.dispatch(lambda p=p: set_rights(p),
                          blocking=False)
        self.join()

        private = (
            '/mnt/docs',
            '/mnt/images',
            '/mnt/work',
            )
        self.ui.info('Setting rights for private data...')
        for p in private:
            self.dispatch(lambda p=p: set_rights(p, dirmask=0o700, filemask=0o600),
                          blocking=False)
        self.join()

        self.ui.info('Checking for inconsistent passwd/group files...')
        try:
            self.dispatch('pwck -qr')
        except self.exc_class:
            pass
        try:
            self.dispatch('grpck -qr')
        except self.exc_class:
            pass
                
        self.ui.info('Checking for sane system file permissions...')
        dir_exceptions = (
            '/home/schweizer/.local', # try to include as much from home as possible
            '/mnt',
            '/usr/portage/distfiles',
            '/var',
            )
        file_exceptions = (
            )
        for root, dirs, files in os.walk('/', onerror=lambda x: self.ui.error(str(x))):
            for d in list(dirs):
                path = os.path.join(root, d)
                if (path in dir_exceptions or
                    os.path.ismount(path)):
                    dirs.remove(d)
            for f in list(files):
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
                        
    @ui.log_exec_time
    def admin_check_portage(self):
        # ====================================================================
        'perform portage maintenance'

        self.ui.info('Checking for potential vulnerabilities...')
        try:
            self.dispatch('glsa-check -ntv all')
        except self.exc_class:
            pass
         
        self.ui.info('Performing useful emaint commands...')
        try:
            self.dispatch('emaint -c all')
        except self.exc_class:
            pass
         
        self.ui.info('Checking for obsolete package.* file entries...')
        try:
            self.dispatch('eix-test-obsolete brief')
        except self.exc_class:
            pass
         
    @ui.log_exec_time
    def admin_check_repos(self):
        # ====================================================================
        'report state of administrative repositories'

        host_branches = [
            'diablo',
            'belial',
        ]
        repos = [
            '/',
            # enables quick & dirty cruft development with emacs
            '/usr/bin',
            '/var/lib/layman/nilathak',
            ]

        for repo in repos:
            self.ui.info('######################### Checking repo at {0}...'.format(repo))
            git_cmd = 'cd ' + repo + ' && git '
            
            # host branch existing?
            try:
                self.dispatch(git_cmd + 'branch | grep ' + self.ui.hostname,
                              output=None, passive=True)
            except self.exc_class:
                host_files = list()
                host_files_diff = list()
            else:
                # Finding host-specific files (host-only + superimposed)
                host_files_actual = self.dispatch(git_cmd + 'diff origin/master ' + self.ui.hostname + ' --name-only', output=None, passive=True).stdout
                # host branches can only modify files from master branch or add new ones
                host_files = self.dispatch(git_cmd + 'diff origin/master ' + self.ui.hostname + ' --name-only --diff-filter=AM', output=None, passive=True).stdout
                host_files.sort()
                host_files_unexpect = set(host_files_actual) - set(host_files)
                if host_files_unexpect:
                    raise self.exc_class('unexpected host-specific diff:' + os.linesep + os.linesep.join(sorted(host_files_unexpect)))
                host_files_diff = self.dispatch(git_cmd + 'diff ' + self.ui.hostname + ' --name-only -- ' + ' '.join(host_files),
                                                output=None, passive=True).stdout

            # Finding master-specific files (common files)
            all_files = self.dispatch(git_cmd + 'ls-files',
                                      output=None, passive=True).stdout
            master_files = list(set(all_files) - set(host_files))
            master_files.sort()
            master_files_diff = self.dispatch(git_cmd + 'diff origin/master --name-only -- ' + ' '.join(master_files),
                                              output=None, passive=True).stdout

            # display repo status
            if host_files_diff:
                host_files_stat = self.dispatch(git_cmd + 'diff ' + self.ui.hostname + ' --name-status -- ' + ' '.join(host_files),
                                                output=None, passive=True).stdout
                self.ui.info('Host status:' + os.linesep + os.linesep.join(host_files_stat))
            if master_files_diff:
                master_files_stat = self.dispatch(git_cmd + 'diff origin/master --name-status -- ' + ' '.join(master_files),
                                                  output=None, passive=True).stdout
                self.ui.info('Master status:' + os.linesep + os.linesep.join(master_files_stat))
                
                # export master changes to avoid checking out master branch instead of host branch
                if host_files:
                    url = self.dispatch(git_cmd + 'config --get remote.origin.url',
                                        output=None, passive=True).stdout[0]
                    clone_path = '/tmp/' + os.path.basename(url)
                    self.ui.info('Preparing temporary master repo for {0} into {1}...'.format(repo, clone_path))
                    try:
                        self.dispatch(git_cmd + 'clone {0} {1}'.format(url, clone_path),
                                      output='stdout')
                    except self.exc_class:
                        pass
                    for f in master_files:
                        self.dispatch('cp {0} {1}'.format(os.path.join(repo, f),
                                                          os.path.join(clone_path, f)),
                                      output='stderr')

            # optionally display repo files
            if self.ui.args.list_files:
                if host_files:
                    self.ui.info('Host files:' + os.linesep + os.linesep.join(host_files))
                if master_files:
                    self.ui.info('Master files:' + os.linesep + os.linesep.join(master_files))
        
    @ui.log_exec_time
    def admin_check_ssd(self):
        # ====================================================================
        'periodic manual SSD maintenance'
        ssd_mount_points = {
            'diablo': (
                '/',
            ),
        }
        for mp in ssd_mount_points[self.ui.hostname]:
            self.dispatch('/sbin/fstrim -v ' + mp)

    @ui.log_exec_time
    def admin_kernel(self):
        # ====================================================================
        'scripting stuff to build kernels'

        key_mp = '/tmp/keyring'
        key_image = '/mnt/work/usb_boot'
        
        self.dispatch('eselect kernel list', passive=True)
        selection = input('which kernel?')
        self.dispatch('eselect kernel set ' + selection)
        os.chdir('/usr/src/linux')
        self.dispatch('make -j' + str(multiprocessing.cpu_count()*2-1), output='nopipes')

        # install kernel to USB keyrings
        try:
            self.dispatch('mkdir ' + key_mp, output='stdout')
        except self.exc_class:
            pass
        try:
            self.dispatch('rm -rf /boot; ln -s {0}/{1} /boot'.format(key_mp, self.ui.hostname), output='stdout')
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

        self.dispatch('mount {0} {1}'.format(part, key_mp))
        self.dispatch('make install')

        self.ui.info('install grub modules + embed into boot sector')
        self.dispatch('grub-install {0} --boot-directory={1}/boot'.format(dev, key_mp))
        if not self.ui.args.no_backup:
            self.ui.info('rsync new grub installation to keyring backup')
            self.dispatch('rsync -a {0}/boot {1} --exclude="/boot/grub/grub.cfg"'.format(key_mp, key_image),
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
            self.dispatch('rsync -av' + dry_run + ' --modify-window 1 --no-perms --no-owner --no-group --inplace --delete {0}/ {1} {2}'.format(key_image,
                                                                                                                                              key_mp,
                                                                                                                                              ' '.join(rsync_exclude)),
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

    def admin_repos_rebase(self):
        # ====================================================================
        'deliver admin repos to github'
        # FIXME
        # - implement fast MD5 check to determine if file is not needed in repo anymore
        # FLOW HOWTO
        # - host MUST NEVER be merged onto master; ALWAYS operate on master directly, or just cherry-pick from host to master
        # - rebasing host with remote master ensures a minimal diffset ("superimposing" files are auto-removed if no longer needed)
        # - avoid host branches on github, since host branches tend to include security-sensitive files
        repos = [
            '/',
            '/usr/bin',
            '/var/lib/layman/nilathak',
            ]

        for repo in repos:
            self.ui.info('Rebasing repo at {0}...'.format(repo))
            git_cmd = 'cd ' + repo + ' && git '
            
            self.dispatch(git_cmd + 'stash',
                          output='stderr')
            self.dispatch(git_cmd + 'pull -r',
                          output='both')
            try:
                self.dispatch(git_cmd + 'stash show',
                              output=None)
            except self.exc_class:
                pass
            else:
                self.dispatch(git_cmd + 'stash pop',
                              output='stderr')

    def admin_rotate_wpa(self):
        # ====================================================================
        'renew random guest access key for wlan'
        # FIXME
        # - regenerate WPA passphrase by generating an OTP from google-authenticator PAM module
        # - generate matching OTP via app on my own phone, and show it to guests for time-limited login
        pass

    def admin_spindown(self):
        # ====================================================================
        'force large HDD into standby mode'

        luks_uuid = 'd6464602-14fc-485c-befc-d22ba8e4d533'
        btrfs_label = 'pool'

        frequency_per_hour = 4
        
        # script will be run via cron.hourly
        for i in range(frequency_per_hour):

            self.ui.debug('checking for ongoing IO operations using a practical hysteresis')
            try:
                device = os.path.basename(self.dispatch('/sbin/findfs UUID=' + luks_uuid,
                                                        output=None, passive=True).stdout[0])
                io_ops_1st = self.dispatch('cat /proc/diskstats | grep ' + device,
                                           output=None, passive=True).stdout[0]
            except self.exc_class:
                raise self.exc_class("Container HDD not found!")
            
            # 60s buffer to next run-crons job
            time.sleep(60 / frequency_per_hour * 59)
            
            io_ops_2nd = self.dispatch('cat /proc/diskstats | grep ' + device,
                                       output=None, passive=True).stdout[0]

            if io_ops_1st == io_ops_2nd:
                self.ui.debug('Ensure filesystem buffers are flushed')

                btrfs_device = self.dispatch('/sbin/findfs LABEL=' + btrfs_label,
                                             output=None, passive=True).stdout[0]
                out = self.dispatch('cat /proc/mounts | grep ' + btrfs_device,
                                    output=None, passive=True).stdout[0]
                dev,mp,*rest = out.split(' ')
                if dev == btrfs_device:
                    self.dispatch('btrfs filesystem sync ' + mp,
                                  output=None)
                    
                self.ui.debug('Spinning down...')
                self.dispatch('hdparm -y /dev/' + device,
                              output=None)

    def admin_sync(self):
        # ====================================================================
        'sync ebuild repositories'
        self.dispatch('emaint sync -A',
                      output='stderr')
        self.dispatch('eix-update',
                      output='stderr')
        self.admin_update()
        
    def admin_update(self):
        # ====================================================================
        'update portage'

        self.ui.info('Checking for updates...')
        try:
            self.dispatch('{0} {1} {2}'.format(
                'emerge --nospinner --autounmask-keep-masks --keep-going --verbose-conflict --with-bdeps=y -uDNv world',
                '-p' if not self.ui.args.force else '',
                '-t' if self.ui.args.tree else ''),
                          output='nopipes')
        except self.exc_class:
            pass
        
        self.ui.info('Checking for obsolete dependencies...')
        self.dispatch('{0} {1}'.format(
            'emerge --depclean',
            '-p' if not self.ui.args.force else ''),
                      output='nopipes')

        if self.ui.args.force:
            self.ui.info('Rebuilding broken lib dependencies...')
            self.dispatch('emerge @preserved-rebuild',
                          output='nopipes')

            self.ui.info('Checking for obsolete distfiles...')
            self.dispatch('eclean -Cd distfiles -f',
                          output='nopipes')

    def admin_wrap(self):
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
                if exc.errno == errno.EEXIST:
                    pass
                else:
                    raise exc
     
        image = '/mnt/work/hypnocube/WRAP.1E.img'
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
            '--exclude="/var/lib/dhcpcd/dhcpcd-wlan0-arreat.lease"',
            '--exclude="/var/lib/openntpd/ntpd.drift"',
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
        if len([x for x in self.dispatch('ps aux | grep admin.py',
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

if __name__ == '__main__':
    app = admin(job_class=job.job,
                ui_class=ui)
    app.run()

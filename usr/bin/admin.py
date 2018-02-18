#!/usr/bin/env python3
'''script collection for system administration
'''
import os
import pylon.base
import pylon.gentoo.job
import pylon.gentoo.ui
import re
import sys
import time

class ui(pylon.gentoo.ui.ui):
    def __init__(self, owner):
        super().__init__(owner)
        self.parser_common.add_argument('-o', '--options', help='pass custom string to operations')
        self.parser_common.add_argument('-f', '--force', action='store_true')
         
        self.init_op_parser()
        self.parser_check_repos.add_argument('-l', '--list_files', action='store_true')
        self.parser_check_repos.add_argument('-r', '--rebase', action='store_true')
        self.parser_games.add_argument('-c', '--cfg', action='store_true')
        self.parser_kernel.add_argument('--no-backup', action='store_true', help='do not overwrite keyring content with backup')
        self.parser_kernel.add_argument('-s', '--small', action='store_true', help='skip rsync of large ISOs')
        self.parser_update.add_argument('-s', '--sync', action='store_true')
        self.parser_update.add_argument('-t', '--tree', action='store_true')
        self.parser_wine.add_argument('-c', '--cfg', action='store_true')
        self.parser_wrap.add_argument('-s', '--sync', action='store_true', help='sync to device')

    def setup(self):
        super().setup()
        if not self.args.op:
            self.parser.print_help()
            raise self.owner.exc_class('Specify at least one subcommand operation')
        
class admin(pylon.base.base):
    __doc__ = sys.modules[__name__].__doc__
    
    def run_core(self):
        getattr(self, self.__class__.__name__ + '_' + self.ui.args.op)()

    @pylon.log_exec_time
    def admin_check_audio(self):
        # ====================================================================
        'check audio metadata (low bitrates, ...)'
        import json
        
        dir_exceptions = (
            '.stfolder',
            '0_sort',
            'ringtones',
            )
        file_exceptions = (
            '.stignore',
        )
        walk = self.ui.args.options or '/mnt/audio'

        media_files = list()
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            [dirs.remove(d) for d in list(dirs) for x in dir_exceptions if re.search(x, os.path.join(root, d))]
            [dirs.remove(d) for d in list(dirs) if os.path.ismount(os.path.join(root, d))]
            [files.remove(f) for f in list(files) for x in file_exceptions if re.search(x, os.path.join(root, f))]
            media_files += (os.path.join(root, x) for x in files)
            if not dirs:
                if ('cover.jpg' not in files and
                    'cover.png' not in files):
                    self.ui.warning('No album cover detected: {0}'.format(root))

        # process file list in chunks to avoid: Argument list is too long
        for chunk in pylon.chunk(1000, media_files):
            out = self.dispatch('/usr/bin/exiftool -j "{0}"'.format('" "'.join(chunk)),
                                output=None).stdout
            for file_dict in json.loads(os.linesep.join(out)):
                filetype = file_dict['FileType']
                file     = file_dict['SourceFile']
                
                if filetype == 'MP3' or filetype == 'OGG':
                    bitrate = file_dict.get('AudioBitrate') or file_dict['NominalBitrate']
                    # ignore unit specification
                    bitrate = float(bitrate.split()[-2])
                    if bitrate < 130:
                        self.ui.warning('Low audio bitrate detected: {1} ({0:-6f})'.format(bitrate, file))

                elif filetype == 'JPEG':
                    x,y = file_dict['ImageSize'].split('x')
                    if int(x) < 500 or int(y) < 500:
                        self.ui.warning('Low resolution (< 500x500) cover detected: {0}'.format(file))

    @pylon.log_exec_time
    def admin_check_btrfs(self):
        # ====================================================================
        'periodic manual btrfs maintenance'

        btrfs_config = {
            'diablo': {
                'external': ('/run/media/schweizer/external', False),
                'offline': ('/mnt/work/backup/offline', False),
                'online': ('/mnt/work/backup/online', True),
            },
            'belial': {
                'belial': ('/', False),
            },
        }

        def job(label, config):
            mp = config[0]
            ssd = config[1]

            # http://marc.merlins.org/perso/btrfs/post_2014-03-19_Btrfs-Tips_-Btrfs-Scrub-and-Btrfs-Filesystem-Repair.html
            # Even in 4.3 kernels, you can still get in places where balance won't work (no space left, until you run a -m0 one first)
            for percent in (x-1 for x in pylon.unique_logspace(10, 79)):
                self.ui.info('Balancing metadata + data chunks with {1}% usage for {0}...'.format(label, percent))
                self.dispatch('/usr/bin/nice -10 /sbin/btrfs balance start -musage={0} -dusage={0} {1}'.format(percent, mp))
            
            self.ui.info('Scrubbing {0}...'.format(label))
            self.dispatch('/usr/bin/nice -10 /sbin/btrfs scrub start -Bd ' + mp)

            if ssd:
                self.ui.info('Trimming {0}...'.format(label))
                self.dispatch('/sbin/fstrim -v ' + mp)
            
            self.ui.info('Final usage stats for {0}...'.format(label))
            self.dispatch('/sbin/btrfs fi usage ' + mp,
                          passive=True)
            
        for label,config in btrfs_config[self.ui.hostname].items():
            if not self.ui.args.options or label in self.ui.args.options.split(','):
                self.dispatch(job, label=label, config=config,
                              blocking=False)
        self.join()

        # - FIXME perform periodic defrag after "Defrag/mostly OK/extents get unshared" is completely fixed (https://btrfs.wiki.kernel.org/index.php/Status)
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

    @pylon.log_exec_time
    def admin_check_docs(self):
        # ====================================================================
        'check data consistency on docs'

        # FIXME ensure Octave compatibility:
        # find /mnt/docs/0_sort/ -type f | grep '.*\.m$'
        # /mnt/docs/education/thesis/fpcore/units/fpcoreblks/source/matlab/fpcoreblks/fpcoreblks/slblocks.m
        # /mnt/docs/systems/communication/hsse00_nat_exercises
        # /mnt/docs/systems/dsp/fir iir/example_for_iir_analysis.m
        # /mnt/docs/systems/control/hsse00_ret_exercises
        # /mnt/docs/systems/dsp/hsse00_set5_exercises
        # /mnt/docs/systems/dsp/hsse00_syt4_exercises
        # /mnt/docs/systems/dsp/hsse01_syt4_exercises
        # /mnt/docs/systems/dsp/noiseshaping
        # ??? mnt/docs/systems/modeling/matlab/MSystem
        # ??? mnt/docs/systems/modeling/matlab/mdt
        
        dir_exceptions = (
            )
        file_exceptions = (
            )
        sidecar_pdf_expected = re.compile(r'\.doc(x)?$|\.nb$|\.ppt(x)?$|\.vsd(x)?$|\.xls(x)?$', re.IGNORECASE)
        sidecar_pdf_wo_extension_expected = re.compile(r'exercise.*\.tex$', re.IGNORECASE)
        walk = self.ui.args.options or '/mnt/docs'
        
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            [dirs.remove(d) for d in list(dirs) for x in dir_exceptions if re.search(x, os.path.join(root, d))]
            [dirs.remove(d) for d in list(dirs) if os.path.ismount(os.path.join(root, d))]
            [files.remove(f) for f in list(files) for x in file_exceptions if re.search(x, os.path.join(root, f))]
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

    @pylon.log_exec_time
    def admin_check_filenames(self):
        # ====================================================================
        'check for names incompatible with other filesystems'
        import collections
        
        dir_exceptions = (
            '/mnt/audio/0_sort/0_blacklist',
            '/mnt/games/0_sort/0_blacklist',
            '/mnt/games/wine',
            '/mnt/video/0_sort/0_blacklist',
            )
        file_exceptions = (
            )

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
            
        walk = self.ui.args.options or '/mnt'
        
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            [dirs.remove(d) for d in list(dirs) for x in dir_exceptions if re.search(x, os.path.join(root, d))]
            [dirs.remove(d) for d in list(dirs) if os.path.ismount(os.path.join(root, d))]
            [files.remove(f) for f in list(files) for x in file_exceptions if re.search(x, os.path.join(root, f))]
            names = dirs + files
            names.sort()
            lower_case_dupe_map = collections.Counter(x.lower() for x in names)
            for name in names:
                if (ntfs_invalid_names.search(name) or
                    ntfs_invalid_chars.search(name) or
                    ntfs_invalid_trailing_chars.search(name) or
                    len(name) > 255 or
                    any(ord(c) < 32 for c in name)):
                    self.ui.warning('NTFS incompatible filesystem object: ' + os.path.join(root, name))

        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            [dirs.remove(d) for d in list(dirs) if os.path.ismount(os.path.join(root, d))]
            names = dirs + files
            names.sort()
            lower_case_dupe_map = collections.Counter(x.lower() for x in names)
            for name in names:
                if lower_case_dupe_map[name.lower()] > 1:
                    self.ui.warning('Filesystem objects only distinguished by case: ' + os.path.join(root, name))
                    
    @pylon.log_exec_time
    def admin_check_filetypes(self):
        # ====================================================================
        'check for expected/unexpected filetypes on fileserver'

        # - match nothing in python: a^
        # - avoid uppercase extensions to avoid issues with restrictive flters in GUI dialogs
        #   find /mnt/images/ -type f | grep '\.JPG$' | sed 's/\(.*\)JPG/mv "\1JPG" "\1jpg"/'
        allowed_global = re.compile('a^')
        allowed = {
            'audio': re.compile('\.flac$|\.mp3$|\.ogg$|cover\.(jpg|png)$'),
            
            'docs': re.compile('$|'.join([
                # ebooks
                '\.epub','\.opf',
                # main doc format
                '\.pdf',
                # calibre stuff
                '/0_calibre/.*/cover.jpg', '/0_calibre/metadata.*',
                
                # latex
                '\.tex','\.sty','/images/.*\.png',
                # C/C++
                '\.c(pp)?','\.h','\.inl',
                # Matlab
                '\.m','\.mat','\.mdl','\.spt',
                # HDLs
                '\.vhd',
                # PSpice files
                'hardware/analog/.*\.(cir|prb|sch)',
                # MS office formats (check_docs ensures existing sidecar pdfs)
                '\.vsd(x)?','\.xls(x)?','\.ppt(x)?','\.doc(x)?',
                # Mathematica notebooks (check_docs ensures existing sidecar pdfs)
                '\.nb',
                
                # KMyMoney db
                '/finance/kmymoney_data\.kmy',
                # xray images
                '/(0_helga|health)/.*\.(iso|jpg)',
                # imports that should stay as-is
                '/education/thesis/cdrom/docs/Priest.html',
                '/hardware/digital/verification.*/labs/.*',
                '/software/highlevel/0_hsse00_sen.*\.txt',
                '/systems/communication/hsse00_nat_exercises/hsse00_nat_exercise06/nrec_viterbi\.dll',
                '/systems/dsp/noiseshaping/hk_mash/.*',
                '/systems/modeling/matlab/mdt/.*',
                '/systems/.*\.(raw|sfk|wav)',
                
                # exclude obscure file formats from university tools
                '/hardware/digital/design/0_hsse00_.*\.(txd|ass)', # PROL16 files
                '/hardware/layout/0_hsse00_res5/.*', # layout exercises
                '/software/lowlevel/0_hsse00_sen3/.*Makefile.*', # AVR makefiles
                '/software/lowlevel/0_hsse00_tin.*', # assembler exercises

                # FIXME remove after 0_sort cleanup
                '/0_sort/competition/.*',
                '/0_sort/0_approved/systems/dsp, low_power/Grünigen, Digitale Signalverarbeitung/.*',
            ]) + '$'),
            
            'games': re.compile('.*'),
            
            'images': re.compile('\.' + '$|\.'.join([
                # uncompressed
                'gif','png',
                # compressed
                'jpg',
                # camera videos
                'avi','mov','mpg',
            ]) + '$'),
            
            'video': re.compile('\.' + '$|\.'.join([
                # subtitles
                'idx','srt','sub',
                # bluray/dvd files (DVD files must be uppercase for kodi)
                'BUP','IFO','iso','VOB',
                # video container
                'avi','flv','mkv','mp4','mpg',
            ]) + '$'),
            
            'work': re.compile('.*'),
            }

        forbidden_global = re.compile('sync-conflict|/~[^~]*\.tmp$', re.IGNORECASE)
        forbidden = {
            'audio': re.compile('/0_sort/0_blacklist/.*'),
            'docs': re.compile('a^'),
            'games': re.compile('/0_sort/0_blacklist/.*'),
            'images': re.compile('a^'),
            'video': re.compile('/0_sort/0_blacklist/.*'),
            'work': re.compile('a^'),
            }
        
        dir_exceptions = (
            )
        file_exceptions = (
            '/mnt/docs/.stignore',
            '/mnt/images/.stignore',
        )
        for k in allowed.keys():
            for root, dirs, files in os.walk(os.path.join('/mnt', k), onerror=lambda x: self.ui.error(str(x))):
                [dirs.remove(d) for d in list(dirs) for x in dir_exceptions if re.search(x, os.path.join(root, d))]
                [dirs.remove(d) for d in list(dirs) if os.path.ismount(os.path.join(root, d))]
                [files.remove(f) for f in list(files) for x in file_exceptions if re.search(x, os.path.join(root, f))]
                for f in files:
                    name = os.path.join(root, f)
                    if ((not allowed_global.search(name) and
                         not allowed[k].search(name)) or
                        forbidden_global.search(name) or
                        forbidden[k].search(name)):
                        self.ui.warning('Unexpected filetype detected: ' + name)
                        
    @pylon.log_exec_time
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
            '.stignore',
        )
        walk = self.ui.args.options or '/mnt/images'

        metadata = dict()
        for root, dirs, files in os.walk(walk, onerror=lambda x: self.ui.error(str(x))):
            [dirs.remove(d) for d in list(dirs) for x in dir_exceptions if re.search(x, os.path.join(root, d))]
            [dirs.remove(d) for d in list(dirs) if os.path.ismount(os.path.join(root, d))]
            #[files.remove(f) for f in list(files) for x in file_exceptions if re.search(x, os.path.join(root, f))]
            combined_regex = re.compile('|'.join(file_exceptions))
            abs_paths = (os.path.join(root, x) for x in files)
            excl_paths = (x for x in abs_paths if not re.search(combined_regex, x))
            quoted_paths = ['"{0}"'.format(path) for path in excl_paths]
            if quoted_paths:
                out = self.dispatch('/usr/bin/exiftool -Orientation {0}'.format(' '.join(quoted_paths)),
                                    output=None).stdout
                orientation_tags = out[1::2]
                metadata.update(zip(excl_paths, orientation_tags))
        print(metadata)
            #while(self.chunk(10, excl_paths)):
            #    out = self.dispatch('/usr/bin/exiftool -Orientation {0}'.format(' '.join(excl_paths)),
            #                        output=None).stdout
            #        if len()out:
            #            self.ui.warning('No orientation flag: {0}'.format(path))
            #        elif 'normal' not in out[0]:
            #            self.ui.info('{0} {1}'.format(out, path))

            #for d in dirs:
            #    dir_from_album_root = os.path.join(root, d).replace(walk, '').strip('/')
            #    dir_wo_metachars = dir_from_album_root.replace('/', '_').replace(' ', '_')
            #    self.dispatch('/usr/bin/exiftool -q -P "-FileName<CreateDate" -d "{0}_%%Y-%%m-%%d_%%H-%%M-%%S%%%%-c.%%%%e" "{1}"'.format(dir_wo_metachars, os.path.join(root, d)))
            ## check for missing CreateDate tag
            #for f in files:
            #    if len(self.dispatch('/usr/bin/exiftool -CreateDate "{0}"'.format(os.path.join(root, f)),
            #                         None).stdout) == 0:
            #        self.ui.warning('Missing CreateDate tag for: ' + os.path.join(root, f))

    @pylon.log_exec_time
    def admin_check_network(self):
        # ====================================================================
        'ensure network settings are sane'

        self.ui.debug('Searching for open TCP/UDP ports...')
        self.dispatch('nmap -sT -O localhost').stdout
        
        # FIXME search for open ports with nmap and filter known ones
        # currently open ports:
        # nmap -sT -O localhost
        # PORT     STATE SERVICE
        # 25/tcp   open  smtp (postfix)
        # 53/tcp   open  domain (dnsmasq)
        # 80/tcp   open  http (apache)
        # 110/tcp  open  pop3 (dovecot)
        # 139/tcp  open  netbios-ssn (samba)
        # 143/tcp  open  imap (dovecot)
        # 443/tcp  open  https (apache)
        # 445/tcp  open  microsoft-ds (samba)
        # 631/tcp  open  ipp (cups)
        # 993/tcp  open  imaps (dovecot)
        # 995/tcp  open  pop3s (dovecot)
        # 5500/tcp open  hotline (clementine remote)
        # 6881/tcp open  bittorrent-tracker (qbittorrent)
        # 8080/tcp open  http-proxy (dbus)
        # 9090/tcp open  zeus-admin (calibre)
        # 53/udp   open          domain
        # 67/udp   open|filtered dhcps
        # 68/udp   open|filtered dhcpc
        # 137/udp  open          netbios-ns
        # 138/udp  open|filtered netbios-dgm
        # 5353/udp open|filtered zeroconf
        
        # FIXME search for clients in all local subnets and filter known ones
        # FIXME grep logs for openvpn logins
        
    @pylon.log_exec_time
    def admin_check_permissions(self):
        # ====================================================================
        'check and apply access rights on system & user data paths'
        import stat
        
        def set_perm(path, owner, group, mask):
            os.chown(path, owner, group)
            os.chmod(path, mask)

        def set_perms(tree,
                      owner=1000,  # schweizer
                      group=100,   # users
                      dirmask=0o750,
                      filemask=0o640):
            dir_exceptions = (
                '/mnt/games/linux', # avoid stripping exec permissions
                '/mnt/games/wine', # avoid stripping exec permissions
                )
            file_exceptions = (
                )
            for root, dirs, files in os.walk(tree, onerror=lambda x: self.ui.error(str(x))):
                [dirs.remove(d) for d in list(dirs) for x in dir_exceptions if re.search(x, os.path.join(root, d))]
                [dirs.remove(d) for d in list(dirs) if os.path.ismount(os.path.join(root, d))]
                [files.remove(f) for f in list(files) for x in file_exceptions if re.search(x, os.path.join(root, f))]
                if not self.ui.args.dry_run:
                    [set_perm(os.path.join(root, d), owner, group, dirmask) for d in dirs]
                    [set_perm(os.path.join(root, f), owner, group, filemask) for f in files]
        
        public = (
            '/mnt/audio',
            '/mnt/games',
            '/mnt/video',
            )
        self.ui.info('Setting rights for public data...')
        for p in public:
            self.dispatch(set_perms, tree=p,
                          blocking=False)
        self.join()

        private = (
            '/mnt/docs',
            '/mnt/images',
            #'/mnt/work', just dont, otherwise new git repos will show a complete diff
            )
        self.ui.info('Setting rights for private data...')
        for p in private:
            self.dispatch(set_perms, tree=p, dirmask=0o700, filemask=0o600,
                          blocking=False)
        self.join()

        self.ui.info('Checking for inconsistent passwd/group files (fix with pwck & grpck)...')
        try:
            self.dispatch('/usr/sbin/pwck -qr')
        except self.exc_class:
            pass
        try:
            self.dispatch('/usr/sbin/grpck -qr')
        except self.exc_class:
            pass
                
        self.ui.info('Checking for sane system file permissions...')
        dir_exceptions = (
            '/home/schweizer/.local', # try to include as much from home as possible
            '/mnt',
            '/var',
            )
        file_exceptions = (
            )
        for root, dirs, files in os.walk('/', onerror=lambda x: self.ui.error(str(x))):
            [dirs.remove(d) for d in list(dirs) for x in dir_exceptions if re.search(x, os.path.join(root, d))]
            [dirs.remove(d) for d in list(dirs) if os.path.ismount(os.path.join(root, d))]
            [files.remove(f) for f in list(files) for x in file_exceptions if re.search(x, os.path.join(root, f))]

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
                        
    @pylon.log_exec_time
    def admin_check_portage(self):
        # ====================================================================
        'check portage sanity'

        self.ui.info('Checking for vanished unstable versions...')
        try:
            self.dispatch('EIX_LIMIT=0 /usr/bin/eix -I | grep "^\[?"')
        except self.exc_class:
            pass
        
        self.ui.info('Checking for potential vulnerabilities...')
        try:
            self.dispatch('/usr/bin/glsa-check -ntv all')
        except self.exc_class:
            pass
         
        self.ui.info('Performing useful emaint commands...')
        try:
            self.dispatch('/usr/sbin/emaint -c all')
        except self.exc_class:
            pass
         
        self.ui.info('Checking for obsolete package.* file entries...')
        try:
            self.dispatch('/usr/bin/eix-test-obsolete brief')
        except self.exc_class:
            pass
         
    @pylon.log_exec_time
    def admin_check_repos(self):
        # ====================================================================
        'report state of administrative git repositories (+ optional rebase)'
        # FIXME
        # - implement fast MD5 check to determine if file is not needed in repo anymore (finish md5 check in cruft.py first, then re-use here)
        # REBASE FLOW HOWTO
        # - host MUST NEVER be merged onto master; ALWAYS operate on master directly, or just cherry-pick from host to master
        # - rebasing host with remote master ensures a minimal diffset ("superimposing" files are auto-removed if no longer needed)
        # - avoid host branches on github, since host branches tend to include security-sensitive files

        repos = [
            '/',
            # enables quick & dirty cruft development with emacs
            '/usr/bin',
            ]

        for repo in repos:

            if self.ui.args.rebase:
                self.ui.info('Rebasing repo at {0}...'.format(repo))
                git_cmd = 'cd ' + repo + ' && /usr/bin/git '
                self.dispatch(git_cmd + 'stash',
                              output='stderr')
                self.dispatch(git_cmd + 'pull -r',
                              output='both')
                try:
                    self.dispatch(git_cmd + '--no-pager stash show',
                                  output=None)
                except self.exc_class:
                    pass
                else:
                    self.dispatch(git_cmd + 'stash pop',
                                  output='stderr')
            else:
                self.ui.info('######################### Checking repo at {0}...'.format(repo))
                git_cmd = 'cd ' + repo + ' && git '

                # host branch existing?
                try:
                    self.dispatch(git_cmd + 'branch | /bin/grep ' + self.ui.hostname,
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

                    #import gentoolkit.equery.check
                    #import gentoolkit.helpers
                    #import portage
                    #gtk_check = gentoolkit.equery.check.VerifyContents()
                    #gtk_find = gentoolkit.helpers.FileOwner()
                    # 
                    ## assume standard portage tree locatation at /
                    #trees = portage.create_trees()
                    #vardb = trees['/']["vartree"].dbapi
                    # 
                    #host_file_abs = map(lambda x: '/' + x, host_files)
                    #affected_pkg = map(lambda x: x.mycpv, vardb._owners.get_owners(list(host_file_abs)))
                    #print(list(affected_pkg))

                    
                    self.ui.info('Host status:' + os.linesep + os.linesep.join(host_files_stat))
                if master_files_diff:
                    master_files_stat = self.dispatch(git_cmd + 'diff origin/master --name-status -- ' + ' '.join(master_files),
                                                      output=None, passive=True).stdout
                    self.ui.info('Master status:' + os.linesep + os.linesep.join(master_files_stat))

                    # export master changes to avoid checking out master branch instead of host branch
                    if host_files:
                        url = self.dispatch(git_cmd + 'config --get remote.origin_https.url',
                                            output=None, passive=True).stdout[0]
                        clone_path = '/tmp/' + os.path.basename(url)
                        self.ui.info('Preparing temporary master repo for {0} into {1}...'.format(repo, clone_path))
                        try:
                            self.dispatch(git_cmd + 'clone {0} {1}'.format(url, clone_path),
                                          output='stdout')
                        except self.exc_class:
                            pass
                        for f in master_files:
                            self.dispatch('/bin/cp {0} {1}'.format(os.path.join(repo, f),
                                                              os.path.join(clone_path, f)),
                                          output='stderr')

                # optionally display repo files
                if self.ui.args.list_files:
                    if host_files:
                        self.ui.info('Host files:' + os.linesep + os.linesep.join(host_files))
                    if master_files:
                        self.ui.info('Master files:' + os.linesep + os.linesep.join(master_files))
        
    @pylon.log_exec_time
    def admin_games(self):
        # ====================================================================
        'common games loading script'
        # - DOS/WINE: mt32 is always superior to general midi, even with soundfonts: https://en.wikipedia.org/wiki/List_of_MT-32-compatible_computer_games
        # - DOS/WINE: use CM-32L roland midi roms via munt or scummvm (cm-32l is less noisy than mt-32 and adds more sound effects)
        # - LINUX: for GOG install remount tmp with exec: mount -o remount,exec /tmp
        # - WINE: run with -c to create a new wineprefix (don't forget to remove desktop integration links)
        # - WINE: search mounted media for install binaries during -c run
        dos_path = '/mnt/games/dos'
        media_path = '/mnt/games/0_media'
        udisk_path = '/run/media/schweizer'
        wine_path = '/mnt/games/wine'
        
        games = {
            'DOS_ascendancy': {
                'prefix': os.path.join(dos_path, 'Ascendancy'),
                'cmd': 'call ascend',
            },
            'DOS_dune2': {
                'prefix': os.path.join(dos_path, 'Dune 2'),
                'cmd': 'call dune2',
            },
            'DOS_ffmenu': {
                'prefix': '/mnt/work/classics',
                #'cmd': 'FFM1/FFM.EXE FFSET.FFM',
                #'cmd': 'FFM2/FFM.EXE NEU.FFM',
                #'cmd': 'TP/BIN/TURBO.EXE',
                'cmd': 'command',
            },
            'DOS_gabriel_knight2': {
                'prefix': os.path.join(dos_path, 'Gabriel Knight 2'),
                'cmd': '''choice /C:12 Load first (1234) or second (1256) part of disc set? [1/2]?
                          if errorlevel==2 goto second
                          if errorlevel==1 goto first
                          goto end
                          :first
                          imgmount D "{0}/adventures/Gabriel Knight 2/cd1.iso" -t iso
                          imgmount E "{0}/adventures/Gabriel Knight 2/cd2.iso" -t iso
                          imgmount F "{0}/adventures/Gabriel Knight 2/cd3.iso" -t iso
                          imgmount G "{0}/adventures/Gabriel Knight 2/cd4.iso" -t iso
                          goto run
                          :second
                          imgmount D "{0}/adventures/Gabriel Knight 2/cd1.iso" -t iso
                          imgmount E "{0}/adventures/Gabriel Knight 2/cd2.iso" -t iso
                          imgmount F "{0}/adventures/Gabriel Knight 2/cd5.iso" -t iso
                          imgmount G "{0}/adventures/Gabriel Knight 2/cd6.iso" -t iso
                          :run
                          call GK2DOS
                          :end'''.format(media_path),
            },
            'DOS_goblins2': {
                'prefix': os.path.join(dos_path, 'Goblins 2'),
                'cmd': 'call loader',
            },
            'DOS_goblins3': {
                'prefix': os.path.join(dos_path, 'Goblins 3'),
                'conf': '''[cpu]
                           cycles=20000''',
                'cmd': 'call gob3',
            },
            'DOS_kyrandia2': {
                'prefix': os.path.join(dos_path, 'The Legend of Kyrandia 2'),
                'cmd': '''imgmount D "{0}/adventures/The Legend of Kyrandia - The Hand of Fate/hof.iso" -t iso
                          call hofcd'''.format(media_path),
            },
            'DOS_phantasmagoria': {
                'prefix': os.path.join(dos_path, 'Phantasmagoria'),
                'cmd': 'call sierra resource.cfg',
            },
            'DOS_pinball': {
                'prefix': os.path.join(dos_path, 'Pinball Fantasies'),
                'conf': '''[sdl]
                           fullresolution=1280x1024''',
                'cmd': 'call PINBALL.EXE',
            },
            # FIXME replace with GOG?
            'DOS_the_7th_guest': {
                'prefix': os.path.join(dos_path, 'The 7th Guest'),
                'conf': '''[cpu]
                           cycles=10000''',
                'cmd': '''imgmount D "{0}/adventures/The 7th Guest/t7g_1.iso" -t iso
                          call t7g'''.format(media_path),
            },
            'DOS_warcraft1': {
                'prefix': os.path.join(dos_path, 'Warcraft I'),
                'conf': '''[cpu]
                           cycles=20000''',
                'cmd': 'call war',
                # FIXME automatically switch between fluidsynth and mt32 deamons, debug deamon shutdown
                'midi': 'gm',
            },
            'DOS_xcom1': {
                'prefix': os.path.join(dos_path, 'XCOM 1'),
                'cmd': 'call runxcom.bat',
            },
            'DOS_xcom2': {
                'prefix': os.path.join(dos_path, 'XCOM 2'),
                'cmd': 'call terrorcd',
            },
            # ~/.local/share/doublefine/dott/
            'LINUX_day_of_the_tentacle': {
                'cmd': '/mnt/games/linux/Day of the Tentacle Remastered/game/Dott',
            },
            'LINUX_grim_fandango': {
                'cmd': '/mnt/games/linux/Grim Fandango Remastered/game/bin/GrimFandango',
            },
            # ~/.local/share/openxcom/
            'LINUX_openxcom': {
                'cmd': '/usr/bin/openxcom',
            },
            # FIXME directx10 not supported: add <DirectXVersion>9</DirectXVersion> to Engine.ini files (https://appdb.winehq.org/objectManager.php?sClass=version&iId=33234)
            #http://vignette1.wikia.nocookie.net/anno1404guide/images/7/7c/Large_city-340_houses.png/revision/latest?cb=20130906183117
            #http://anno1404.wikia.com/wiki/Patches
            #http://anno1404.wikia.com/wiki/Ships
            #http://anno1404.wikia.com/wiki/Manorial_palace
            #http://anno1404.wikia.com/wiki/Building_layout_strategies
            #http://anno1404.wikia.com/wiki/Bailiwick
            #http://anno1404.wikia.com/wiki/Tournament_arena
            'WINE_anno_1404': {
                'prefix': os.path.join(wine_path, 'Anno 1404'),
                #'cmd_virtual': os.path.join(media_path, 'strategy/Anno 1404/setup_anno_1404_gold_edition_2.01.5010_(13111).exe'),
                #'cmd_virtual': os.path.join(media_path, '0_d3dx9_redist/DXSETUP.exe'),
                'cmd': os.path.join(wine_path, 'Anno 1404/drive_c/GOG Games/Anno 1404 Gold Edition/Anno4.exe'),
            },
            'WINE_anno_1404_venice': {
                'prefix': os.path.join(wine_path, 'Anno 1404'),
                'cmd': os.path.join(wine_path, 'Anno 1404/drive_c/GOG Games/Anno 1404 Gold Edition/Addon.exe'),
            },
            'WINE_braid': {
                'prefix': os.path.join(wine_path, 'Braid'),
                #'cmd_custom': '/usr/bin/wineconsole ' + os.path.join(media_path, 'platformer/Braid/Setup.bat'),
                'cmd': os.path.join(wine_path, 'Braid/drive_c/Program Files (x86)/Braid/braid.exe'),
                #'cmd_virtual': os.path.join(media_path, '0_d3dx9_redist/DXSETUP.exe'),
                'opt': '-language english',
            },
            # FIXME directx10 not supported in wine? (if support is ok 'very high' should not be greyed out)
            'WINE_crysis': {
                'prefix': os.path.join(wine_path, 'Crysis'),
                #'cmd_virtual': os.path.join(media_path, 'shooter/Crysis/setup_crysis_2.0.0.7.exe'),
                #'cmd_virtual': os.path.join(media_path, '0_d3dx9_redist/DXSETUP.exe'),
                'cmd': os.path.join(wine_path, 'Crysis/drive_c/GOG Games/Crysis/Bin32/Crysis.exe'),
                'opt': '-dx9',
            },
            # install failed, copied from native win
            'WINE_dead_space': {
                'prefix': os.path.join(wine_path, 'Dead Space'),
                #'media': [os.path.join(media_path, 'shooter/Dead Space/DEADSPACE.ISO')],
                #'cmd_virtual': os.path.join(udisk_path, 'DEADSPACE/autorun.exe'),
                'cmd': os.path.join(wine_path, 'Dead Space/drive_c/Program Files (x86)/Electronic Arts/Dead Space/Dead Space.exe'),
            },
            'WINE_deponia': {
                'prefix': os.path.join(wine_path, 'Deponia'),
                #'cmd_virtual': os.path.join(media_path, 'adventures/Deponia/setup_deponia_3.3.1357_(16595)_(g).exe'),
                'cmd': os.path.join(wine_path, 'Deponia/drive_c/GOG Games/Deponia/deponia.exe'),
            },
            # FIXME menu is broken, but gameplay is fine: https://bugs.winehq.org/show_bug.cgi?id=2082
            'WINE_diablo': {
                'prefix': os.path.join(wine_path, 'Diablo'),
                'media': [os.path.join(media_path, 'rpg/Diablo/Diablo.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'DIABLO/setup.exe'),
                #'cmd_virtual': os.path.join(media_path, 'rpg/Diablo/drtl109.exe'),
                'cmd': os.path.join(wine_path, 'Diablo/drive_c/Diablo/diablo.exe'),
                'res': '"640x480"',
            },
            # Key d2: HGMH - 228E - 7X2K - 2867
            # Key ld: H2DV - 66PE - 8DT9 - D4KW
            'WINE_diablo2': {
                'prefix': os.path.join(wine_path, 'Diablo II'),
                'media': [os.path.join(media_path, 'rpg/Diablo 2/Cinematics.iso'),
                          os.path.join(media_path, 'rpg/Diablo 2/Install.iso'),
                          os.path.join(media_path, 'rpg/Diablo 2/Lord Of Destruction.iso'),
                          os.path.join(media_path, 'rpg/Diablo 2/Play.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'INSTALL/setup.exe'),
                #'cmd_virtual': os.path.join(udisk_path, 'EXPANSION/setup.exe'),
                #'cmd_virtual': os.path.join(media_path, 'rpg/Diablo 2/LODPatch_114b.exe'),
                'cmd': os.path.join(wine_path, 'Diablo II/drive_c/Program Files (x86)/Diablo II/Game.exe'),
                'res': '"800x600"',
            },
            'WINE_edna_bricht_aus': {
                'prefix': os.path.join(wine_path, 'Edna Bricht Aus'),
                'media': [os.path.join(media_path, 'adventures/Edna Bricht Aus/de-ebaus.iso')],
                #'cmd_virtual': os.path.join(media_path, 'adventures/Edna Bricht Aus/eba_patch_1_1.exe'),
                #'cmd_virtual': os.path.join(udisk_path, 'EDNABRICHTAUS/Setup.exe'),
                'cmd_virtual': os.path.join(wine_path, 'Edna Bricht Aus/drive_c/Program Files (x86)/Xider/Edna Bricht Aus/EbaMain.exe'),
                'res': '"800x600"',
           },
            # FIXME bails out with page fault (staging, non-staging, d3d9, non-d3d9)
            # Key: 035921-492715-312032-5217
            'WINE_emperor': {
                'prefix': os.path.join(wine_path, 'Emperor'),
                'media': [os.path.join(media_path, 'strategy/Emperor/Atreides.iso'),
                          os.path.join(media_path, 'strategy/Emperor/Harkonnen.iso'),
                          os.path.join(media_path, 'strategy/Emperor/Install.iso'),
                          os.path.join(media_path, 'strategy/Emperor/Ordos.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'EMPEROR1/SETUP.EXE'),
                #'cmd_virtual': os.path.join(media_path, 'strategy/Emperor/EM109EN.EXE'),
                'cmd': os.path.join(wine_path, 'Emperor/drive_c/Westwood/Emperor/EMPEROR.EXE'),
                'res': '"1600x1200"',
            },
            # FIXME serial cannot be entered, first field is limited to only 4 chars !?! 
            'WINE_fable': {
                'prefix': os.path.join(wine_path, 'Fable - The Lost Chapters'),
                'media': [os.path.join(media_path, 'rpg/Fable - The Lost Chapters/Fable The Lost Chapters.iso')],
                'cmd_virtual': os.path.join(udisk_path, 'Fable Disk 1/setup.exe'),
                'res': '"800x600"',
            },
            # Ultimate edition contains Dread Lords & both expansion packs: Dark Avatar & Twilight of the Arnor
            # FIXME even after switching back freetype hinting engine, some fonts are still screwed up
            #       switching fontconfig between hintslight/hintnone has no effect (although other apps are clearly affected).
            #       same story with antialiasing
            # explaination for version mess: 1.53 is the final version of the vanilla GC2 "Dread Lords" game.
            # The Community Update has only changed things from the latest xpac "Twilight of Arnor"
            # So you either own TotA as separate extension or the UE, in both cases you simply register your game to the Stardock site &
            # then you should be able to have access to a direct link containing the latest (2.20) version.
            'WINE_galciv2_dread_lords': {
                'prefix': os.path.join(wine_path, 'Galactic Civilizations II Ultimate Edition'),
                #'cmd_virtual': os.path.join(media_path, 'strategy/Galactic Civilizations II Ultimate Edition/setup_galactic_civilizations2_2.0.0.2.exe'),
                #'cmd_virtual': os.path.join(media_path, 'strategy/Galactic Civilizations II Ultimate Edition/patch_galactic_civilizations2_2.1.0.3.exe'),
                #'cmd_virtual': os.path.join(media_path, '0_d3dx9_redist/DXSETUP.exe'),
                'cmd': os.path.join(wine_path, 'Galactic Civilizations II Ultimate Edition/drive_c/GOG Games/Galactic Civilizations II/GalCiv2.exe'),
                'env': 'FREETYPE_PROPERTIES="truetype:interpreter-version=35"',
            },
            'WINE_galciv2_dark_avatar': {
                'prefix': os.path.join(wine_path, 'Galactic Civilizations II Ultimate Edition'),
                'cmd': os.path.join(wine_path, 'Galactic Civilizations II Ultimate Edition/drive_c/GOG Games/Galactic Civilizations II/DarkAvatar/GC2DarkAvatar.exe'),
                'env': 'FREETYPE_PROPERTIES="truetype:interpreter-version=35"',
            },
            'WINE_galciv2_twilight_of_the_arnor': {
                'prefix': os.path.join(wine_path, 'Galactic Civilizations II Ultimate Edition'),
                'cmd': os.path.join(wine_path, 'Galactic Civilizations II Ultimate Edition/drive_c/GOG Games/Galactic Civilizations II/Twilight/GC2TwilightOfTheArnor.exe'),
                'env': 'FREETYPE_PROPERTIES="truetype:interpreter-version=35"',
            },
            'WINE_gobliiins': {
                'prefix': os.path.join(wine_path, 'Gobliiins'),
                #'cmd_virtual': os.path.join(media_path, 'adventures/Gobliiins/setup_gobliiins_2.1.0.64.exe'),
                'cmd': os.path.join(wine_path, 'Gobliiins/drive_c/GOG Games/Gobliiins/ScummVM/scummvm.exe'),
                'opt': '-c "C:\GOG Games\Gobliiins\gobliiins1.ini" gobliiins1',
            },
            # FIXME slow, try medium settings
            'WINE_grim_dawn': {
                'prefix': os.path.join(wine_path, 'Grim Dawn'),
                #'cmd_virtual': os.path.join(media_path, 'rpg/Grim Dawn/setup_grim_dawn_1.0.4.0_hotfix_1_(17257)_(g).exe'),
                'cmd': os.path.join(wine_path, 'Grim Dawn/drive_c/GOG Games/Grim Dawn/Grim Dawn.exe'),
            },
            'WINE_indiana_jones_atlantis': {
                'prefix': os.path.join(wine_path, 'Indiana Jones and the Fate of Atlantis'),
                #'cmd_virtual': os.path.join(media_path, 'adventures/Indiana Jones and the Fate of Atlantis/setup_indiana_jones_and_the_fate_of_atlantis_2.1.0.8.exe'),
                'cmd': os.path.join(wine_path, 'Indiana Jones and the Fate of Atlantis/drive_c/GOG Games/Indiana Jones and the Fate of Atlantis/ScummVM/scummvm.exe'),
                'opt': '-c "C:\GOG Games\Indiana Jones and the Fate of Atlantis\Atlantis.ini" atlantis',
            },
            'WINE_machinarium': {
                'prefix': os.path.join(wine_path, 'Machinarium'),
                #'cmd_virtual': os.path.join(media_path, 'adventures/Machinarium/Machinarium_full_en.exe'),
                'cmd_virtual': os.path.join(wine_path, 'Machinarium/drive_c/Program Files (x86)/Machinarium/machinarium.exe'),
                'res': '"1024x768"',
            },
            # FIXME slow? strange crash after switching x servers
            'WINE_mass_effect': {
                'prefix': os.path.join(wine_path, 'Mass Effect'),
                'media': [os.path.join(media_path, 'rpg/Mass Effect/rld-mass.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'Mass Effect/setup.exe'),
                #'cmd_virtual': os.path.join(media_path, 'rpg/Mass Effect/1.02 patch & crack/MassEffect_EFIGS_1.02.exe'),
                'cmd': os.path.join(wine_path, 'Mass Effect/drive_c/Program Files (x86)/Mass Effect/MassEffectLauncher.exe'),
            },
            # FIXME sound jitters
            'WINE_monkey_island1': {
                'prefix': os.path.join(wine_path, 'Monkey Island 1 - Secret of Monkey Island'),
                'media': [os.path.join(media_path, 'adventures/Monkey Island 1 Special Edition/rld-mise.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'MISE/setup.exe'),
                #'cmd_virtual': os.path.join(media_path, '0_d3dx9_redist/DXSETUP.exe'),
                'cmd': os.path.join(wine_path, 'Monkey Island 1 - Secret of Monkey Island/drive_c/Program Files (x86)/Secret Of Monkey Island SE/MISE.exe'),
            },
            # FIXME sound jitters (less than mi1)
            'WINE_monkey_island2': {
                'prefix': os.path.join(wine_path, 'Monkey Island 2 - LeChucks Revenge'),
                'media': [os.path.join(media_path, 'adventures/Monkey Island 2 Special Edition/sr-mi2se.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'MI2SE/Setup.exe'),
                'cmd': os.path.join(wine_path, 'Monkey Island 2 - LeChucks Revenge/drive_c/Program Files (x86)/LucasArts/Monkey Island 2 LeChucks Revenge Special Edition/Monkey2.exe'),
            },
            # GOG claims inofficial 1.05 patch is already included:
            # https://www.gog.com/forum/psychonauts/psychonauts_patch_1_05_retail_gog_and_other_dd_officially_unofficial_release/page4
            'WINE_psychonauts': {
                'prefix': os.path.join(wine_path, 'Psychonauts'),
                #'cmd_virtual': os.path.join(media_path, 'platformer/Psychonauts/setup_psychonauts_2.2.0.13.exe'),
                'cmd': os.path.join(wine_path, 'Psychonauts/drive_c/GOG Games/Psychonauts/Psychonauts.exe'),
            },
            # FIXME slow
            'WINE_realmyst_masterpiece': {
                'prefix': os.path.join(wine_path, 'RealMyst Masterpiece'),
                #'cmd_virtual': os.path.join(media_path, 'adventures/RealMyst Masterpiece/setup_realmyst_masterpiece_edition_2.1.0.6.exe'),
                'cmd': os.path.join(wine_path, 'RealMyst Masterpiece/drive_c/GOG Games/realMyst Masterpiece Edition/realMyst.exe'),
            },
            # FIXME crash during game start
            # Key: TLAD-JPAC-PGHL-3PTB-32
            'WINE_return_to_castle_wolfenstein': {
                'prefix': os.path.join(wine_path, 'Return to Castle Wolfenstein'),
                'media': [os.path.join(media_path, 'shooter/Return To Castle Wolfenstein/RZR-WOLF.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'rtcw/Setup.exe'),
                'cmd_virtual': os.path.join(wine_path, 'Return to Castle Wolfenstein/drive_c/Program Files (x86)/Return to Castle Wolfenstein/WolfSP.exe'),
            },
            'WINE_siedler2': {
                'prefix': os.path.join(wine_path, 'The Settlers II - 10th Anniversary'),
                'media': [os.path.join(media_path, 'strategy/Die Siedler 2 (10th Anniversary Edition)/rld-sett2.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'SettlersII/setup.exe'),
                'cmd': os.path.join(wine_path, 'The Settlers II - 10th Anniversary/drive_c/Program Files (x86)/Ubisoft/Funatics/The Settlers II - 10th Anniversary/bin/S2DNG.exe'),
            },
            'WINE_simtower': {
                'prefix': os.path.join(wine_path, 'Simtower'),
                #'cmd_virtual': os.path.join(media_path, 'simulator/SimTower/SETUP.EXE'),
                'cmd_virtual': os.path.join(wine_path, 'Simtower/drive_c/SIMTOWER/SIMTOWER.EXE'),
                'res': '"1024x768"',
            },
            'WINE_the_book_of_unwritten_tales': {
                'prefix': os.path.join(wine_path, 'The Book of Unwritten Tales'),
                'media': [os.path.join(media_path, 'adventures/The Book of Unwritten Tales/BoUT.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'BoUT/Setup.exe'),
                'cmd': os.path.join(wine_path, 'The Book of Unwritten Tales/drive_c/Program Files (x86)/Unwritten Tales/bout.exe'),
            },
            'WINE_the_book_of_unwritten_tales_2': {
                'prefix': os.path.join(wine_path, 'The Book of Unwritten Tales 2'),
                'media': [os.path.join(media_path, 'adventures/The Book of Unwritten Tales 2/BoUT2.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'TBOUT2/setup.exe'),
                'cmd': os.path.join(wine_path, 'The Book of Unwritten Tales 2/drive_c/Program Files (x86)/The Book of Unwritten Tales 2/Windows/BouT2.exe'),
            },
            # FIXME resolution needs to be maxed with glide wrapper (bails out), old save games already work!
            'WINE_the_longest_journey': {
                'prefix': os.path.join(wine_path, 'The Longest Journey'),
                'media': [os.path.join(media_path, 'adventures/The Longest Journey/Thelongestjourney.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'TLJ/Setup.exe'),
                #'cmd_virtual': os.path.join(media_path, 'adventures/The Longest Journey/tlj-patch-161.exe'),
                'cmd': os.path.join(wine_path, 'The Longest Journey/drive_c/Program Files (x86)/Funcom/The Longest Journey/Game.exe'),
                #'cmd': os.path.join(wine_path, 'The Longest Journey/drive_c/Program Files (x86)/Funcom/The Longest Journey/dgVoodooCpl.exe'),
            },
            'WINE_the_whispered_world': {
                'prefix': os.path.join(wine_path, 'The Whispered World'),
                #'cmd_virtual': os.path.join(media_path, 'adventures/The Whispered World/setup_the_whispered_world_special_edition_2.1.0.11.exe'),
                'cmd': os.path.join(wine_path, 'The Whispered World/drive_c/GOG Games/The Whispered World SE/twwse.exe'),
            },
            # Key: 628G-GDKW-9B92-VXDZ
            'WINE_warcraft2': {
                'prefix': os.path.join(wine_path, 'Warcraft II'),
                'media': [os.path.join(media_path, 'strategy/Warcraft 2/WIIBNE.iso')],
                #'cmd_virtual': os.path.join(udisk_path, 'WAR2BNECD/setup.exe'),
                'cmd': os.path.join(wine_path, 'Warcraft II/drive_c/Program Files (x86)/Warcraft II BNE/Warcraft II BNE.exe'),
                'res': '"640x480"',
            },
            # FIXME cutscenes are skipped (new 1.28 patch should resolve this, see https://bugs.winehq.org/show_bug.cgi?id=35651)
            'WINE_warcraft3': {
                'prefix': os.path.join(wine_path, 'Warcraft III'),
                #'cmd_virtual': os.path.join(media_path, 'strategy/Warcraft 3 - Reign of Chaos & Frozen Throne/Warcraft III eSK.exe'),
                'cmd_virtual': os.path.join(wine_path, 'Warcraft III/drive_c/Program Files (x86)/Warcraft III Frozen Throne eSK/Warcraft III.exe'),
                'opt': '-opengl -nativefullscr',
            },
            # FIXME cutscenes are skipped (new 1.28 patch should resolve this, see https://bugs.winehq.org/show_bug.cgi?id=35651)
            # Frozen Throne Key: FN7MVD-HHMX-KYPMYD-FTHT-H99GY8
            'WINE_warcraft3_frozen_throne': {
                'prefix': os.path.join(wine_path, 'Warcraft III'),
                #'cmd_virtual': os.path.join(media_path, 'strategy/Warcraft 3 - Reign of Chaos & Frozen Throne/Warcraft III eSK.exe'),
                'cmd_virtual': os.path.join(wine_path, 'Warcraft III/drive_c/Program Files (x86)/Warcraft III Frozen Throne eSK/Frozen Throne.exe'),
                'opt': '-opengl -nativefullscr',
            },
            'WINE_worldofgoo': {
                'prefix': os.path.join(wine_path, 'WorldOfGoo'),
                #'cmd_virtual': os.path.join(media_path, 'simulator/World of Goo/WorldOfGooSetup.1.30.exe'),
                #'cmd_virtual': os.path.join(media_path, '0_d3dx9_redist/DXSETUP.exe'),
                'cmd': os.path.join(wine_path, 'WorldOfGoo/drive_c/Program Files (x86)/WorldOfGoo/WorldOfGoo.exe'),
            },
        }

        ########################## Interface
        sel = self.key_sel_if(games.keys())
        game = games[sel]
        # cd into exe dir, to avoid working directory issues
        xinit_cmd = 'cd "{0}" && /usr/bin/xinit'
        
        ##########################
        # use separate X server, to avoid rearranged plamoids due to resolution change
        if 'DOS' in sel:
            dosbox_conf_str = '''
                [sdl]
                output=opengl
                # only the normal2x scaler is able to scale to full native desktop resolution (with crappy performance)
                # so choose a good & fast x3 scaler for maximum fullscreen resolution (3x original games resolution)
                #fullresolution=2560x1440
                fullscreen=true
                sensitivity=25
                [render]
                # all 4:3 games need aspect correction on current 2560x1440 (16:9) display.
                # a higher scaling factor increases the fullscreen resolution and sharpness (especially when aspect correction is enabled)
                scaler=normal3x
                aspect=true
                [sblaster]
                irq=5
                [midi]
                # fluidsynth to be independent of ALSA wavetable support; check devices with aconnect -lio
                midiconfig=128:0
                [speaker]
                pcspeaker=false
                [dos]
                keyboardlayout=gr
                {1}
                [autoexec]
                @echo off
                mount C "{0}"
                C:
                {2}
                exit'''.format(game['prefix'], game['conf'] if 'conf' in game else '', game['cmd'])

            #self.dispatch('fluidsynth -si ' + os.path.join(dos_path, '0_soundfonts/Real_Font_V2.1.sf2'),
            #              blocking=False, daemon=True)

            with open('/tmp/dosbox.conf', 'w') as dosbox_conf:
                dosbox_conf.write(dosbox_conf_str)
            dosbox_cmd = '/usr/bin/dosbox -conf /tmp/dosbox.conf'
            self.dispatch(' '.join([xinit_cmd.format(dos_path), dosbox_cmd, '-- :1 vt8']))

            # FIXME fluidsynth is not properly killed, even with daemon=True
            #self.join(timeout=1)
            
        ##########################
        if 'LINUX' in sel:
            # FIXME gog games: game screen clipped when started via xinit
            self.dispatch('cd "{0}" && "{1}"'.format(os.path.dirname(game['cmd']), game['cmd']))
            #self.dispatch(' '.join([xinit_cmd.format(os.path.dirname(game['cmd'])), '"{0}"'.format(game['cmd']), '-- :1 vt8']))
        
        ##########################
        elif 'WINE' in sel:
            self.wine_wrapper(game)
            
    def wine_wrapper(self, app):
        import contextlib 
        import dbus # supported on diablo only

        bus = dbus.SystemBus()
        udisks2_manager_obj = bus.get_object('org.freedesktop.UDisks2', '/org/freedesktop/UDisks2/Manager')
        media = None
        res = app.get('res', '"2560x1440"')
        xorg_conf_str = '''
        Section "Monitor"
            Identifier             "Monitor0"
        EndSection
        Section "Device"
            Identifier             "Device0"
        EndSection
        Section "Screen"
            Identifier             "Screen0"
            Device                 "Device0"
            Monitor                "Monitor0"
            DefaultDepth           24
            SubSection             "Display"
                Depth              24
                Modes              {0}
            EndSubSection
        EndSection
        Section "InputClass"
            Identifier             "system-keyboard"
            Option                 "XkbLayout"  "de"
            Option                 "XkbModel"   "pc101"
            Option                 "XkbVariant" "nodeadkeys"
        EndSection
        '''.format(res)

        # cd into exe dir, to avoid working directory issues
        xinit_cmd = 'cd "{0}" && /usr/bin/xinit'

        with open('/tmp/xorg.conf', 'w') as xorg_conf:
            xorg_conf.write(xorg_conf_str)

        # disable winemenubuilder to prevent programs from creating menu entries and file associations
        wine_cmd_common_prefix = '/usr/bin/env {1} WINEPREFIX="{0}" WINEDLLOVERRIDES=winemenubuilder.exe=d WINEDEBUG=warn'.format(app['prefix'], app['env'] if 'env' in app.keys() else '')

        if self.ui.args.cfg:
            cmd = '{0} {1}'.format(wine_cmd_common_prefix, '/usr/bin/winecfg')
        else:
            cmd_type = next(k for k,v in app.items() if k.startswith('cmd'))
            wine_cmd_virtual_desktop = 'explorer /desktop=name'
            wine_cmd_opt = app['opt'] if 'opt' in app.keys() else ''
            
            wine_cmd_common = '{0} {1}'.format(wine_cmd_common_prefix, '/usr/bin/wine')
            app_cmd = app[cmd_type]
            working_dir = os.path.dirname(app_cmd)
            if cmd_type == 'cmd_virtual':
                wine_cmd = ' '.join([wine_cmd_common, wine_cmd_virtual_desktop + ',' + res, '"' +  app_cmd + '"', wine_cmd_opt])
            else:
                if cmd_type == 'cmd_custom':
                    wine_cmd_common = '{0} {1}'.format(wine_cmd_common_prefix, app['cmd_custom'])
                    app_cmd = ''
                    working_dir = app['prefix']
                wine_cmd = ' '.join([wine_cmd_common, '"' +  app_cmd + '"', wine_cmd_opt])

            cmd = ' '.join([xinit_cmd.format(working_dir), wine_cmd, '-- :1 -config xorg.wine.conf vt8'])

        if 'media' in app.keys():
            with contextlib.ExitStack() as stack:
                media = [stack.enter_context(open(medium, 'r+b')) for medium in app['media']]
                udisks2_manager = dbus.Interface(udisks2_manager_obj, 'org.freedesktop.UDisks2.Manager')
                mounts = list()
                for medium in media:
                    loop_obj_path = udisks2_manager.LoopSetup(medium.fileno(), {})
                    loop_obj = bus.get_object('org.freedesktop.UDisks2', loop_obj_path)
                    filesystem = dbus.Interface(loop_obj, 'org.freedesktop.UDisks2.Filesystem')
                    filesystem.Mount({})
                    mounts.append((filesystem, loop_obj))
                try:
                    self.dispatch(cmd)
                finally:
                    for filesystem,loop_obj in mounts:
                        filesystem.Unmount({})
                        loop = dbus.Interface(loop_obj, 'org.freedesktop.UDisks2.Loop')
                        loop.Delete({})
        else:
            self.dispatch(cmd)

    @pylon.log_exec_time
    def admin_kernel(self):
        # ====================================================================
        'scripting stuff to build kernels'
        import multiprocessing
        
        key_mp = '/tmp/keyring'
        key_image = '/mnt/work/usb_boot'
        
        self.dispatch('/usr/bin/eselect kernel list', passive=True)
        selection = input('which kernel?')
        self.dispatch('/usr/bin/eselect kernel set ' + selection)
        os.chdir('/usr/src/linux')
        self.dispatch('/usr/bin/make -j' + str(multiprocessing.cpu_count()*2-1), output='nopipes')

        # install kernel to USB keyrings
        try:
            self.dispatch('/bin/mkdir ' + key_mp, output='stdout')
        except self.exc_class:
            pass
        try:
            self.dispatch('/bin/rm -rf /boot; /bin/ln -s {0}/{1} /boot'.format(key_mp, self.ui.hostname), output='stdout')
        except self.exc_class:
            pass

        part = self.dispatch('/sbin/findfs LABEL=KEYRING', passive=True, output=None).stdout[0]
        dev = part[:-1]

        # umount KDE mounts as well, since parallel mounts might lead to ro remounting 
        try:
            while True:
                self.dispatch('/bin/umount ' + part, passive=True, output='stdout')
        except self.exc_class:
            pass

        self.ui.info('perform automatic offline fsck')
        try:
            self.dispatch('/usr/sbin/fsck.vfat -a ' + part)
        except self.exc_class:
            pass

        self.dispatch('/bin/mount {0} {1}'.format(part, key_mp))
        self.dispatch('/usr/bin/make install')

        self.ui.info('install grub modules + embed into boot sector')
        self.dispatch('/usr/sbin/grub-install {0} --boot-directory={1}/boot'.format(dev, key_mp))
        if not self.ui.args.no_backup:
            self.ui.info('rsync new grub installation to keyring backup')
            self.dispatch('/usr/bin/rsync -a {0}/boot/grub/ {1}/boot/grub/ --exclude="grub.cfg"'.format(key_mp, key_image),
                          output='both')
        self.ui.info('install host-specific grub.cfg (grub detects underlying device and correctly uses absolute paths to kernel images)')
        self.dispatch('/usr/sbin/grub-mkconfig -o /boot/grub/grub.cfg')

        if not self.ui.args.no_backup:
            self.ui.info('rsync keyring backup to actual device')
            rsync_exclude = [
                # kernels & grub.cfgs
                '--exclude="/diablo"',
            ]
            rsync_exclude_small = [
                '--exclude="/*.iso"',
            ]
            if self.ui.args.small:
                rsync_exclude += rsync_exclude_small 
            dry_run = ''
            if not self.ui.args.force:
                self.ui.info('Use -f to apply sync!')
                dry_run = 'n'
            self.dispatch('/usr/bin/rsync -av{3} --modify-window 1 --no-perms --no-owner --no-group --inplace --delete {0}/ {1} {2}'.format(key_image,
                                                                                                                                            key_mp,
                                                                                                                                            ' '.join(rsync_exclude),
                                                                                                                                            dry_run),
                          output='both')

        try:
            while True:
                self.dispatch('/bin/umount ' + part, passive=True, output='stdout')
        except self.exc_class:
            pass

        self.dispatch('/bin/rm /boot')
        self.ui.info('Rebuild kernel modules')
        self.dispatch('/usr/bin/make modules_install')
        self.dispatch('/usr/bin/emerge @module-rebuild', output='nopipes')

    def admin_luks_container(self):
        # ====================================================================
        'access luks container via udisks2'
        import sys
        
        from PyQt5.QtWidgets import QApplication, QInputDialog, QLineEdit
        # application needed before any widget creation
        app = QApplication(sys.argv)

        # import supported on diablo only
        import dbus
        
        bus = dbus.SystemBus()
        udisks2_manager_obj = bus.get_object('org.freedesktop.UDisks2', '/org/freedesktop/UDisks2/Manager')

        with open('/mnt/work/luks/container', 'r+b') as container:
            udisks2_manager = dbus.Interface(udisks2_manager_obj, 'org.freedesktop.UDisks2.Manager')
            loop_obj_path = udisks2_manager.LoopSetup(container.fileno(), {})
            try:
                loop_obj = bus.get_object('org.freedesktop.UDisks2', loop_obj_path)
                loop = dbus.Interface(loop_obj, 'org.freedesktop.UDisks2.Encrypted')
                retry = True
                valid = False

                while(retry):
                    passphrase, retry = QInputDialog.getText(None, 'Decryption', 'Passphrase', QLineEdit.Password)
                    if retry:
                        try:
                            decrypt_obj_path = loop.Unlock(passphrase, {})
                            retry = False
                            valid = True
                        except dbus.exceptions.DBusException:
                            pass
                if valid:
                    try:
                        decrypt_obj = bus.get_object('org.freedesktop.UDisks2', decrypt_obj_path)
                        decrypt_uuid = decrypt_obj.Get('org.freedesktop.UDisks2.Block', 'IdUUID', dbus_interface='org.freedesktop.DBus.Properties')
                        decrypt_device = self.dispatch('/sbin/findfs UUID=' + decrypt_uuid,
                                                       output=None, passive=True).stdout[0]
                        try:
                            self.dispatch('/sbin/fsck.ext3 -p ' + decrypt_device, output=None)
                        except Exception as e:
                            print(e)
                            time.sleep(10)
                        decrypt = dbus.Interface(decrypt_obj, 'org.freedesktop.UDisks2.Filesystem')
                        mount_path = decrypt.Mount({})
                        self.dispatch('/usr/bin/dolphin ' + mount_path, output=None)
                        decrypt.Unmount({})
                    finally:
                        loop.Lock({})
            finally:
                loop = dbus.Interface(loop_obj, 'org.freedesktop.UDisks2.Loop')
                loop.Delete({})

    def admin_spindown(self):
        # ====================================================================
        'force offline HDD array into standby mode'

        # import supported on diablo only
        import psutil
        
        disks = ('/dev/disk/by-id/ata-WDC_WD60EFRX-68L0BN1_WD-WX11D3743LU8',
                 '/dev/disk/by-id/ata-WDC_WD60EFRX-68MYMN1_WD-WX41DA427KFT')
        mount_point = '/mnt/work/backup/offline'
                
        # script will be run asynchronously by various cron scripts, so ensure the disks are really idle
        self.ui.debug('checking for ongoing IO operations using a practical hysteresis')

        # monitor activity of a single RAID1 device
        sd_path = os.path.realpath(disks[0])
        partition = sd_path.split('/')[-1]+'1'
        io_ops_1st = str(psutil.disk_io_counters(True)[partition])
            
        time.sleep(5*60)

        io_ops_2nd = str(psutil.disk_io_counters(True)[partition])
        if io_ops_1st == io_ops_2nd:
            self.ui.debug('Ensure filesystem buffers are flushed')
            self.dispatch('/sbin/btrfs filesystem sync ' + mount_point,
                          output=None)
            self.ui.debug('Spinning down...')
            for disk in disks:
                self.dispatch('/sbin/hdparm -y ' + disk,
                              output=None)

    def admin_update(self):
        # ====================================================================
        'update portage'

        if self.ui.args.sync:
            git_cmd = 'cd /usr/portage && /usr/bin/git '
            self.ui.info('Synchronizing repositories...')
            self.dispatch('/usr/sbin/emaint sync -A',
                          output='stderr')
            
            # - automatically push result of successful rebase only on diablo (origin remote uses ssh protocol)
            # - sync is always started manually from root shell, thus ssh-agent should provide key
            # - push all local branches (pull requests!) not only master
            if self.ui.hostname == 'diablo':
                self.dispatch(git_cmd + 'push origin',
                              output='stderr')
            self.dispatch('/usr/bin/eix-update',
                          output='stderr')
        
        self.ui.info('Checking for updates...')
        try:
            self.dispatch('{0} {1} {2} {3}'.format(
                '/usr/bin/emerge --nospinner --keep-going -uDNv world',
                '-p' if not self.ui.args.force else '',
                '-t' if self.ui.args.tree else '',
                self.ui.args.options if self.ui.args.options else ''),
                          output='nopipes')
        except self.exc_class:
            pass
        
        self.ui.info('Checking for obsolete dependencies...')
        self.dispatch('{0} {1}'.format(
            '/usr/bin/emerge --depclean',
            '-p' if not self.ui.args.force else ''),
                      output='nopipes')

        if self.ui.args.force:
            self.ui.info('Rebuilding broken lib dependencies...')
            self.dispatch('/usr/bin/emerge @preserved-rebuild',
                          output='nopipes')

    @pylon.log_exec_time
    def admin_wine(self):
        # ====================================================================
        'common wine app loading script'
        # - run with -c to create a new wineprefix (don't forget to remove desktop integration links)
        
        apps = {
            'hjsplit': {
                'prefix': '/mnt/work/software/split',
                'cmd_virtual': '/mnt/work/software/split/hjsplit.exe',
                #'cmd_custom': '/usr/bin/msiexec /i /mnt/work/software/Hyperlapse.inst/Hyperlapse-Pro-for-64-bit-Windows_1.6-116-d4cb262-2.msi',
                #'cmd': '/mnt/work/software/Hyperlapse/drive_c/Program Files (x86)/Microsoft Hyperlapse Pro/Microsoft.Research.Hyperlapse.Desktop.exe',
            },
            # FIXME err:module:import_dll Library MSVCR120_CLR0400.dll (which is needed by L"C:\\windows\\Microsoft.NET\\Framework64\\v4.0.30319\\clr.dll") not found
            # try next: installing dotnet 4.0 before 4.5, otherwise run it on company notebook
            'hyperlapse': {
                'prefix': '/mnt/work/software/Hyperlapse',
                'cmd_virtual': '/mnt/work/software/Hyperlapse.inst/NDP452-KB2901907-x86-x64-AllOS-ENU.exe',
                #'cmd_custom': '/usr/bin/msiexec /i /mnt/work/software/Hyperlapse.inst/Hyperlapse-Pro-for-64-bit-Windows_1.6-116-d4cb262-2.msi',
                #'cmd': '/mnt/work/software/Hyperlapse/drive_c/Program Files (x86)/Microsoft Hyperlapse Pro/Microsoft.Research.Hyperlapse.Desktop.exe',
            },
        }
        self.wine_wrapper(apps[self.key_sel_if(apps.keys())])
            
    def admin_wrap(self):
        # ====================================================================
        'mount wrap image for local administration'

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
            ('/mnt', '/mnt'),
            ('/proc', '/proc'),
            ('/run', '/run'),
            ('/sys', '/sys'),
            ('/tmp', '/tmp'),
            )
     
        # first instance does mounting
        os.makedirs(local, exist_ok=True)
        try:
            self.dispatch('/bin/mount | /bin/grep ' + local,
                          output=None, passive=True)
        except self.exc_class:
            self.dispatch('/bin/mount ' + image + ' ' + local,
                          output='stderr')
            for (src, dest) in bind_map:
                os.makedirs(src, exist_ok=True)
                self.dispatch('/bin/mount -o bind {0} {1}'.format(src, os.path.join(local, dest.strip('/'))),
                              output='stderr')
     
        self.ui.info('Entering the chroot...')

        # run batch commands
        opts = ''
        if self.ui.args.options:
            opts = "c '{0}'".format(self.ui.args.options)

        try:
            # chname for chroot hostname modification needs CONFIG_UTS_NS=y
            self.dispatch('/usr/bin/env -i HOME=$HOME $HOSTNAME={0} TERM=$TERM /usr/bin/chname {0} /usr/bin/linux32 /usr/bin/chroot {1} /bin/bash -l{2}'.format(device, local, opts),
                          output='nopipes')
        except self.exc_class:
            self.ui.warning('chroot shell exited with error status (last cmd failed?)')
        self.ui.info('Leaving the chroot...')
     
        # last instance does umounting
        if len([x for x in self.dispatch('/bin/ps aux | /bin/grep admin.py',
                                         output=None,
                                         passive=True).stdout if ' wrap' in x]) == 1:
            for (src, dest) in reversed(bind_map):
                self.dispatch('/bin/umount ' + os.path.join(local, dest.strip('/')),
                              output='stderr')
     
            try:
                if self.ui.args.sync:
                    self.ui.info('Syncing changes to device...')
                    try:
                        self.dispatch('/bin/ping ' + device + ' -c 1',
                                      output='stderr')
                        try:
                            self.dispatch('/usr/bin/rsync -aHv --delete ' + local + '/ ' + device + ':/ ' + ' '.join(rsync_exclude),
                                          output='both')
                            self.ui.info('Updating grub in native environment...')
                            self.dispatch('/usr/bin/ssh {0} /usr/sbin/grub-install /dev/sda'.format(device),
                                          output='both')
                            self.dispatch('/usr/bin/ssh {0} /usr/sbin/grub-mkconfig -o /boot/grub/grub.cfg'.format(device),
                                          output='both')
                        except self.exc_class:
                            self.ui.warning('Something went wrong during the rsync process...')
                    except self.exc_class:
                        self.ui.warning('Device is offline, changes are NOT synced...')
            finally:
                self.dispatch('/usr/bin/sleep 0.2 && /bin/umount ' + local,
                              output='stderr')
        else:
            self.ui.warning('No other device chroot environment should be open while doing rsync, close them...')

    def key_sel_if(self, keys):
        import readline
        class MyCompleter(object):
            def __init__(self, options):
                self.options = sorted(options)
            def complete(self, text, state):
                if state == 0:  # on first trigger, build possible matches
                    if text:  # cache matches (entries that start with entered text)
                        self.matches = [s for s in self.options if text in s]
                    else:  # no text entered, all matches possible
                        self.matches = self.options[:]
                # return match indexed by state
                try: 
                    return self.matches[state]
                except IndexError:
                    return None
        completer = MyCompleter(keys)
        readline.set_completer(completer.complete)
        readline.parse_and_bind('tab: complete')
        return input("Selection? Use TAB!: ")

if __name__ == '__main__':
    app = admin(job_class=pylon.job.job,
                ui_class=ui)
    app.run()

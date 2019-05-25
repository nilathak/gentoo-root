#!/usr/bin/env python3
'''script collection for system administration
'''
import json
import os
import pylon.base
import pylon.gentoo.job
import pylon.gentoo.ui
import re
import shlex
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
        self.parser_kernel.add_argument('-s', '--small', action='store_true', help='skip rsync of large ISOs')
        self.parser_update.add_argument('-s', '--sync', action='store_true')
        self.parser_wrap.add_argument('-s', '--sync', action='store_true', help='sync to device')

    def setup(self):
        super().setup()
        if not self.args.op:
            self.parser.print_help()
            raise self.owner.exc_class('Specify at least one subcommand operation')
        
class admin(pylon.base.base):
    __doc__ = sys.modules[__name__].__doc__
    
    def run_core(self):
        getattr(self, f'{self.__class__.__name__}_{self.ui.args.op}')()

    def walk(self, root,
             file_excl = list(),
             dir_excl = list()):
        for root, dirs, files in os.walk(root, onerror=lambda x: self.ui.error(str(x))):
            [dirs.remove(d) for d in list(dirs) for x in dir_excl if re.search(x, os.path.join(root, d))]
            [dirs.remove(d) for d in list(dirs) if os.path.ismount(os.path.join(root, d))]
            [files.remove(f) for f in list(files) for x in file_excl if re.search(x, os.path.join(root, f))]
            [files.remove(f) for f in list(files) if not os.path.isfile(os.path.join(root, f))]
            yield root, dirs, files
            
    @pylon.log_exec_time
    def admin_check_audio(self):
        # ====================================================================
        'check audio metadata (low bitrates, ...)'
        
        dir_excl = (
            '.stfolder',
            '0_sort',
            'ringtones',
            )
        file_excl = (
            '.stignore',
        )
        min_specs = {
            '/comedy/'    : [127, 500],
            '/downtempo/' : [191, 800],
            '/electronic/': [191, 800],
            '/metal/'     : [191, 800],
        }
        walk = self.ui.args.options or '/mnt/audio'

        def media_files():
            for root, dirs, files in self.walk(walk, file_excl, dir_excl):
                if not dirs and ('cover.jpg' not in files):
                    self.ui.warning(f'No album cover detected: {root}')
                yield from (shlex.quote(os.path.join(root, f)) for f in files)

        # process file list in chunks to avoid: Argument list is too long
        for chunk in pylon.chunk(1000, media_files()):
            out = self.dispatch(f'/usr/bin/exiftool -j {" ".join(chunk)}',
                                output=None).stdout
            for file_dict in json.loads(os.linesep.join(out)):
                specs    = [v for k,v in min_specs.items() if k in file_dict['SourceFile']][0]
                filetype = file_dict['FileType']
                file     = file_dict['SourceFile']
                
                if filetype == 'MP3' or filetype == 'OGG':
                    bitrate = file_dict.get('AudioBitrate') or file_dict['NominalBitrate']
                    # ignore unit specification
                    bitrate = float(bitrate.split()[-2])
                    if bitrate < specs[0]:
                        self.ui.warning(f'Low audio bitrate detected: {file} ({bitrate:-6f})')

                elif filetype == 'JPEG':
                    x,y = file_dict['ImageSize'].split('x')
                    if int(x) < specs[1] or int(y) < specs[1]:
                        self.ui.warning(f'Low resolution (< {specs[1]}x{specs[1]}) cover detected: {file}')
                        
    @pylon.log_exec_time
    def admin_check_btrfs(self):
        # ====================================================================
        'periodic manual btrfs maintenance'

        # balance & scrub
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

            for percent in (x-1 for x in pylon.unique_logspace(10, 79)):
                self.ui.info(f'Balancing metadata + data chunks with {percent}% usage for {label}...')
                self.dispatch(f'/usr/bin/ionice -c3 /sbin/btrfs balance start -musage={percent} -dusage={percent} {mp}')

            self.ui.info(f'Scrubbing {label}...')
            # scrub already defaults to idle io class
            self.dispatch(f'/sbin/btrfs scrub start -Bd {mp}')

            if ssd:
                self.ui.info(f'Trimming {label}...')
                self.dispatch(f'/sbin/fstrim -v {mp}')
            
            self.ui.info(f'Final usage stats for {label}...')
            self.dispatch(f'/sbin/btrfs fi usage {mp}',
                          passive=True)

        for label,config in btrfs_config[self.ui.hostname].items():
            if not self.ui.args.options or label in self.ui.args.options.split(','):
                self.dispatch(job, label=label, config=config,
                              blocking=False)
        self.join()

        # defrag all files in root subvolume with 1000+ extents
        extent_pattern = re.compile(' [0-9]{4,} extent')
        
        def quoted_files(walk):
            for root, dirs, files in self.walk(walk):
                yield from (shlex.quote(os.path.join(root, f)) for f in files)

        def defrag_job(chunk):
            for idx,l in enumerate(self.dispatch(f'/usr/sbin/filefrag {" ".join(list(chunk))}',
                                                 output=None).stdout):
                match = extent_pattern.search(l)
                if match:
                    self.ui.warning(match.string)
                    self.dispatch(f'/usr/bin/ionice -c3 /sbin/btrfs filesystem defrag -f {chunk[idx]}')

        for chunk in pylon.chunk(100, quoted_files('/')):
            self.dispatch(defrag_job, chunk=chunk,
                          blocking=False)
        self.join()
                
    @pylon.log_exec_time
    def admin_check_docs(self):
        # ====================================================================
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
        'check data consistency on docs'
        
        sidecar_pdf_expected = re.compile(r'\.doc(x)?$|\.nb$|\.ppt(x)?$|\.vsd(x)?$|\.xls(x)?$', re.IGNORECASE)
        sidecar_pdf_wo_extension_expected = re.compile(r'exercise.*\.tex$', re.IGNORECASE)
        walk = self.ui.args.options or '/mnt/docs'

        for root, dirs, files in self.walk(walk):
            for f in files:
                sidecar = f'{f}.pdf'
                sidecar_wo_extension = f'{os.path.splitext(f)[0]}.pdf'
                if (sidecar_pdf_expected.search(f) and not sidecar in files or
                    sidecar_pdf_wo_extension_expected.search(f) and not sidecar_wo_extension in files):
                    self.ui.warning(f'Sidecar PDF expected for: {os.path.join(root, f)}')
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
        
        dir_excl = (
            '/mnt/audio/0_sort/0_blacklist',
            '/mnt/games/0_sort/0_blacklist',
            '/mnt/games/wine',
            '/mnt/video/0_sort/0_blacklist',
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

        for root, dirs, files in self.walk(walk, dir_excl=dir_excl):
            names = dirs + files
            names.sort()
            lower_case_dupe_map = collections.Counter(x.lower() for x in names)
            for name in names:
                if (ntfs_invalid_names.search(name) or
                    ntfs_invalid_chars.search(name) or
                    ntfs_invalid_trailing_chars.search(name) or
                    len(name) > 255 or
                    any(ord(c) < 32 for c in name)):
                    self.ui.warning(f'NTFS incompatible filesystem object: {os.path.join(root, name)}')

        for root, dirs, files in self.walk(walk):
            names = dirs + files
            names.sort()
            lower_case_dupe_map = collections.Counter(x.lower() for x in names)
            for name in names:
                if lower_case_dupe_map[name.lower()] > 1:
                    self.ui.warning(f'Filesystem objects only distinguished by case: {os.path.join(root, name)}')
                    
    @pylon.log_exec_time
    def admin_check_filetypes(self):
        # ====================================================================
        'check for expected/unexpected filetypes on fileserver'

        # - match nothing in python: a^
        # - avoid uppercase extensions to avoid issues with restrictive flters in GUI dialogs
        allowed_global = re.compile('a^')
        allowed = {
            'audio': re.compile('\.flac$|\.mp3$|\.ogg$|cover\.jpg$'),
            
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
                '/hardware/verification.*/labs/.*',
                '/software/highlevel/0_hsse00_sen.*\.txt',
                '/systems/communication/hsse00_nat_exercises/hsse00_nat_exercise06/nrec_viterbi\.dll',
                '/systems/dsp/noiseshaping/hk_mash/.*',
                '/systems/modeling/matlab/mdt/.*',
                '/systems/.*\.(raw|sfk|wav)',
                
                # exclude obscure file formats from university tools
                '/hardware/design/0_hsse00_.*\.(txd|ass)', # PROL16 files
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
                'avi','mov','mp4','mpg',
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
        
        file_excl = (
            '/mnt/docs/.stignore',
            '/mnt/images/.stignore',
        )
        for k in allowed.keys():
            for root, dirs, files in self.walk(os.path.join('/mnt', k), file_excl=file_excl):
                for f in (os.path.join(root, x) for x in files):
                    if ((not allowed_global.search(f) and
                         not allowed[k].search(f)) or
                        forbidden_global.search(f) or
                        forbidden[k].search(f)):
                        self.ui.warning(f'Unexpected filetype detected: {f}')
                        
    @pylon.log_exec_time
    def admin_check_images(self):
        # ====================================================================
        'check image metadata (silently convert to xmp)'

        # FIXME
        # - search for dupes in digikam, after sorting is complete
        # - search for images with usercomment/description/title/rating, ensure no garbage is included (even without the invalid encoding error)
        # - ensure all viewers can play videos
        # - cut rome videos, normalize sound at least
        # - whatsapp profile foto
        # - find ../../johann*/ -type f | grep -i img

        # basic ideas
        # - exclude external web pics, restrict to JPG files
        # - convert original EXIF to XMP tags, delete EXIF and any other metadata format afterwards
        # - if EXIF/XMP need to be updated synchronously => use MWG tags 
        #   https://sno.phy.queensu.ca/~phil/exiftool/TagNames/MWG.html
        #   ensure viewer apps support at least mwg:description and mwg:rating
        # - rename all files based on mwg:createdate => basic sorting mode for all viewer apps
        #   - even ok for scanned images, as the order in which they were scanned is often the chronological order in the paper album
        #   - if known, manually change createdate of scanned images to actual date of paper pic
        #   - use filemodifydate for video files, dont play around with video metadata

        # manual import flow
        # - create new folder or copy into existing (extension will be converted to lower case automatically)
        # - run check_images with -o <folder>
        # - fix invalid createdate, run check_images again
        # - remove invalid tags
        # - optional: add description & rating (digikam: caption, f-stop gallery: details->edit description)
        
        # EXIFTOOL MANUAL FLOW QUICK REFERENCE
        # - add standard caption
        #   digikam: displayed and nice editor (configurable write-back to EXIF/XMP)
        #   gwenview: displayed, field needs to be enabled, FIXME enable correct fields after globally removing EXIF
        #   f-stop gallery: displayed, writes to XMP.dc.description, XMP.xmp.rating
        #   exiftool <common_args> -mwg:description="string" <file>
        # - change creation date
        #   exiftool <common_args> -mwg:createdate="1990:02:01 00:00:00" <file>
        #   exiftool <common_args> -createdate+="0:1:1 00:00:00" <file> (add 1 month, 1 day)
        # - problematic tags
        #   - Warning: Invalid EXIF text encoding for
        #     exiftool <common_args> -usercomment= <files>
        #   - Warning: [minor] Error reading PreviewImage from file
        #     exiftool <common_args> -previewimage= <files>
        #   - Unknown metadata "APP14"
        #     the JPEG APP14 "Adobe" group is not removed by default with "-All=" because it may affect the appearance of the image.
        #     exiftool <common_args> -Adobe:All= <files>
        #   - get rid of problematic EXIF/XMP tags by rebuilding metadata
        #     exiftool <common_args> -all= -tagsfromfile @ -all:all -unsafe -icc_profile <files>
       
        dir_excl = (
            '/mnt/images/0_sort',
            '/mnt/images/cartoons',
            '/mnt/images/cuteness',
            '/mnt/images/design',
            '/mnt/images/fun',
        )
        walk = self.ui.args.options or '/mnt/images'

        all_files = set()
        for root, dirs, files in self.walk(walk, dir_excl=dir_excl):
            all_files.update(os.path.join(root, x) for x in files)

        image_extensions = re.compile('\.' + '$|\.'.join([
                'jpg',
            ]) + '$')
        video_extensions = re.compile('\.' + '$|\.'.join([
                'avi','mov','mp4','mpg',
            ]) + '$')
        image_files = set(x for x in all_files if image_extensions.search(x, re.IGNORECASE))
        video_files = set(x for x in all_files if video_extensions.search(x, re.IGNORECASE))
        for unknown in (all_files - image_files - video_files):
            self.ui.warning(f'Unknown file type detected: "{unknown}"')

        # ignore minor errors and downgrade them warnings ([minor])
        common_args = '-duplicates -ignoreMinorErrors -overwrite_original -preserve -quiet'
        
        known_metadata = (
            'exif',
            'makernotes', # leave it, will overlay with EXIF/XMP but more camera info might be interesting
            'xmp',
        )
        obsolete_metadata = (
            'exiftool',   # no real tags
            'file',       # no real tags
            'flashpix',   # obsolete
            'iptc',       # concentrate on EXIF/XMP
            'jfif',       # obsolete
            'mpf',        # obsolete
            'printim',    # obsolete
            'sourcefile', # no real tags
        )
        unwanted_tags = (
            'gps:all',      # strip all GPS info
            'xmp:geotag',   # not covered by gps tag
            'mwg:keywords', # used for tagging, which I'll never use
            'xmp:keywords', # not covered by MWG tag
        )
        suspicious_xmp_tags = (
            'ImageDescription', # coming from EXIF, should be removed since xmp.dc.description is used in viewers
            'Title',            # not used in viewers
        )
        def excl_tags_str(tags):
            return ' '.join(f'--{x}:all' for x in tags)
        def delete_tags_str(tags):
            return ' '.join(f'-{x}=' for x in tags)
        def quoted_paths_str(paths):
            return ' '.join(f'"{x}"' for x in paths)
        def rename_common(chunk, images):
            rename_dict = dict()
            date_source = 'mwg:createdate' if images else 'filemodifydate'
            for f in chunk:
                key = os.path.abspath(os.path.dirname(f)).replace('/mnt/images/', '')
                rename_dict.setdefault(key, list()).append(f)
            for k in rename_dict.keys():
                # FIXME used for quick manual chronological sorting of scanned images, remove afterwards
                if self.ui.args.force:
                    self.dispatch('/usr/bin/exiftool {0} "-mwg:createdate<filename" -d "{1}_%Y-%m-%d_%H-%M-%S%%-c.%%le" {2}'.format(
                        common_args,
                        k.replace(os.sep, '_').replace(' ', '_'),
                        quoted_paths_str(rename_dict[k])))
                else:
                    self.dispatch('/usr/bin/exiftool {0} "-filename<{3}" -d "{1}_%Y-%m-%d_%H-%M-%S%%-c.%%le" {2}'.format(
                        common_args,
                        k.replace(os.sep, '_').replace(' ', '_'),
                        quoted_paths_str(rename_dict[k]),
                        date_source))
        
        def image_job(chunk):
            self.ui.info('Performing metadata sanity checks...')
            out = self.dispatch('/usr/bin/exiftool -e -g -j -n -u {0} {1} {2}'.format(
                common_args,
                excl_tags_str(obsolete_metadata),
                quoted_paths_str(chunk)),
                                output=None, passive=True).stdout
            chunk_exif = list()
            for file_dict in json.loads(os.linesep.join(out)):
                fix_date = True
                for k in file_dict.keys():
                    if k == 'EXIF':
                        chunk_exif.append(file_dict['SourceFile'])
                    if k == 'EXIF' or k == 'XMP':
                        if 'CreateDate' in file_dict[k]:
                            fix_date = False
                    if k == 'XMP':
                        for tag in suspicious_xmp_tags:
                            if tag in file_dict[k]:
                                self.ui.warning('Suspicious tag "{0}" detected in "{1}"'.format(tag, file_dict['SourceFile']))
                    if k.lower() not in (known_metadata + obsolete_metadata):
                        self.ui.warning('Unknown metadata "{0}" detected in "{1}"'.format(k, file_dict['SourceFile']))
                if fix_date:
                    self.ui.warning('Fixing missing "CreateDate" tag in "{0}"'.format(file_dict['SourceFile']))
                    self.dispatch('/usr/bin/exiftool {0} "-mwg:createdate<filemodifydate" "{1}"'.format(
                        common_args,
                        file_dict['SourceFile']))
                        
            self.ui.info('Deleting unknown metadata structures...')
            self.dispatch('/usr/bin/exiftool -all= {0} {1} {2}'.format(
                common_args,
                excl_tags_str(known_metadata),
                quoted_paths_str(chunk)))

            if chunk_exif:
                self.ui.info('Migrating EXIF to XMP...')
                self.dispatch('/usr/bin/exiftool  -@ /usr/share/exiftool/arg_files/exif2xmp.args {0} {1}'.format(
                    common_args,
                    quoted_paths_str(chunk_exif)),
                              
                              # files with incompletely migratable EXIF will report on stderr 'Warning: No writable tags set from'
                              # usually not a problem, eg written by digikam during rating
                              output='stdout')
                
                self.ui.info('Deleting EXIF...')
                self.dispatch('/usr/bin/exiftool -exif:all= {0} {1}'.format(
                    common_args,
                    quoted_paths_str(chunk_exif)))

            self.ui.info('Removing specific metadata tags...')
            # - cannot be combined with previous exiftool calls: "Once excluded from the output, a tag may not be re-included by a subsequent option"
            self.dispatch('/usr/bin/exiftool {0} {1} {2}'.format(
                common_args,
                delete_tags_str(unwanted_tags),
                quoted_paths_str(chunk)))

            self.ui.info('Renaming files...')
            rename_common(chunk, True)
            
            # FIXME rotation handling
            # - report wrong rotation via mwg:orientation
            # - automatic rotation by exiftool? rotated in other tools, eg pets_stanz_2010-09-24_15-43-59.JPG
            # - check whether orientation flag is missing, what's the output of imagemagick then?
            #out = self.dispatch('/usr/bin/exiftool -Orientation {0}'.format(' '.join(quoted_paths)),
            #                    output=None).stdout
            #orientation_tags = out[1::2]
            #metadata.update(zip(abs_paths, orientation_tags))
            #print(list(v for k,v in metadata.items() if not re.search('normal', v)))

        def video_job(chunk):
            self.ui.info('Renaming video files...')
            rename_common(chunk, False)
            
        # process file list in chunks to avoid: Argument list is too long
        for chunk in pylon.chunk(100, image_files):
            self.dispatch(image_job, chunk=chunk,
                          blocking=False)
        for chunk in pylon.chunk(100, video_files):
            self.dispatch(video_job, chunk=chunk,
                          blocking=False)
        self.join()

    @pylon.log_exec_time
    def admin_check_network(self):
        # ====================================================================
        'ensure network settings are sane'

        self.ui.info('Searching for open TCP/UDP ports...')

        # FIXME test output of -sN; -sF; -sX; -sO
        
        xml_lines = self.dispatch('nmap -sSU -O localhost -oX -',
                                  output='stdout').stdout

        import xml.dom.minidom
        
        from xml.dom.minidom import DOMImplementation
        #imp = xml.dom.minidom.DOMImplementation()
        #doctype = imp.createDocumentType(
        #    qualifiedName='nmap.dtd',
        #    publicId='', 
        #    systemId='/usr/share/nmap/nmap.dtd',
        #)
        dom = xml.dom.minidom.parseString(os.linesep.join(xml_lines))
        ports_node = dom.getElementsByTagName("ports")[0]
        port_nodes = ports_node.getElementsByTagName("port")
        state_nodes = ports_node.getElementsByTagName("state")
        
        print(port_list)
        
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
        'check and apply access rights on system & user data'
        import itertools
        import stat

        # uid, gid, dirmask, filemask
        perm_dict = {
            'users_rw':  (1000, 100, 0o770, 0o660),
            'users_r':   (1000, 100, 0o750, 0o640),
            'schweizer': (1000, 100, 0o700, 0o600),
        }

        # tuple is reversed for matching
        perm_tree = (
            ('/mnt/audio',                 'users_r'),
            ('/mnt/audio/0_sort',          'users_rw'),
            ('/mnt/docs',                  'users_r'),
            ('/mnt/docs/.*',               'schweizer'),
            ('/mnt/docs/0_calibre',        'users_r'),
            ('/mnt/docs/0_sort',           'users_rw'),
            ('/mnt/games',                 'users_r'),
            ('/mnt/games/0_sort',          'users_rw'),
            ('/mnt/games/dos/.*',          'users_rw'), # other users => saving games
            ('/mnt/games/linux',           None),       # could be users_r when exec permissions are retained
            ('/mnt/games/snes',            'users_rw'),
            ('/mnt/games/wine',            None),       # could be users_rw when exec permissions are retained
            ('/mnt/images',                'users_r'),
            ('/mnt/images/0_sort',         'users_rw'),
            ('/mnt/video',                 'users_r'),
            ('/mnt/video/0_sort',          'users_rw'),
            ('/mnt/video/porn',            'schweizer'),
            ('/mnt/work',                  'schweizer'),
            ('/mnt/work/.*',               None),       # problematic for github repos and software executables (parent dir permission locks all anyway)
        )

        def set_perm(path, perm_key, mask_idx):
            perms = perm_dict[perm_key]
            if self.ui.args.dry_run:
                self.ui.debug(f'{perm_key:>10} {path}')
            else:
                os.chown(path, perms[0], perms[1])
                os.chmod(path, perms[mask_idx])

        self.ui.info('Setting filesystem permissions...')
        for root, dirs, files in self.walk('/mnt'):
            [dirs.remove(d) for d in list(dirs) for x,y in reversed(perm_tree) if re.search(x, os.path.join(root, d)) and not y]
         
            for path in itertools.chain(zip(dirs, itertools.repeat(2)), zip(files, itertools.repeat(3))):
                for branch, perm_key in reversed(perm_tree):
                    if re.search(branch, os.path.join(root, path[0])):
                        set_perm(os.path.join(root, path[0]), perm_key, path[1])
                        break
        
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
        dir_excl = (
            '/home',
            '/mnt',
            '/var',
            )
        for root, dirs, files in self.walk('/', dir_excl=dir_excl):
            for d in (os.path.join(root, x) for x in dirs):
                if (os.stat(d).st_mode & stat.S_IWGRP or
                    os.stat(d).st_mode & stat.S_IWOTH):
                    self.ui.warning(f'Found world/group writeable dir: {d}')

            for f in (os.path.join(root, x) for x in files):
                try:
                    if (os.stat(f).st_mode & stat.S_IWGRP or
                        os.stat(f).st_mode & stat.S_IWOTH):
                        self.ui.warning(f'Found world/group writeable file: {f}')

                    if (os.stat(f).st_mode & stat.S_ISGID or
                        os.stat(f).st_mode & stat.S_ISUID):
                        if (os.stat(f).st_nlink > 1):
                            # someone may try to retain older versions of binaries, eg avoiding security fixes
                            self.ui.warning(f'Found suid/sgid file with multiple links: {f}')
                except FileNotFoundError:
                    # dead links are reported by cruft anyway
                    pass
                        
    @pylon.log_exec_time
    def admin_check_portage(self):
        # ====================================================================
        'check portage sanity'

        import portage
        trees = portage.create_trees()
        vardb = trees[portage.settings['EROOT']]["vartree"].dbapi
        build_times = list()
        for pkg in vardb.cpv_all():
            build_time = int(vardb.aux_get(pkg, ['BUILD_TIME'])[0])
            build_times.append((pkg, build_time))
        import operator
        build_times.sort(key = operator.itemgetter(1), reverse=True)
        not_rebuilt_since = [x for x in build_times if time.time() - x[1] > 3600*24*365]
        if not_rebuilt_since:
            self.ui.warning(f'{len(not_rebuilt_since)} packages have not been compiled for 1 year!')
            for x in not_rebuilt_since:
                self.ui.ext_info(f'{time.ctime(x[1])} - {x[0]}')

        import _emerge.actions
        for repo in _emerge.actions.load_emerge_config().target_config.settings.repositories:
            self.ui.info(f'Checking {repo.name} repository for local modifications...')
            self.dispatch(f'cd {repo.location} && /usr/bin/git status -s')
        
        self.ui.info('Checking for potential vulnerabilities...')
        try:
            self.dispatch('/usr/bin/glsa-check -ntv all')
        except self.exc_class:
            pass
         
        self.ui.info('Performing useful emaint commands...')
        try:
            self.dispatch('/usr/sbin/emaint -f all')
            self.dispatch('/usr/sbin/emaint -c all')
        except self.exc_class:
            pass
         
        self.ui.info('Checking for obsolete package.* file entries...')
        try:
            env = ('EIX_LIMIT=0',
                   # manually clean-up mask file, an upstream mask suddenly making mine redundant rarely happens
                   'REDUNDANT_IF_IN_MASK=false',
                   'REDUNDANT_IF_MASK_NO_CHANGE=false',
                   # only report a redundant keywords entry if an installed version is not masked
                   'REDUNDANT_IF_NO_CHANGE=+some-installed',
                   # report obsolete use configurations for uninstalled packages
                   'REDUNDANT_IF_IN_USE=-some',
            )
            self.dispatch(f'{" ".join(env)} /usr/bin/eix-test-obsolete')
        except self.exc_class:
            pass
         
    @pylon.log_exec_time
    def admin_check_repos(self):
        # ====================================================================
        'report state of administrative git repositories'
        # - no full absolute file path for status reporting is intended => ensures native git commands are only executed in matching repo dir
        # FLOW HOWTO
        # - host MUST NEVER be merged onto master; ALWAYS operate on master directly, or cherry-pick from host to master
        # - avoid host branches on github, since host branches include security-sensitive files

        repos = (
            ('/', True),
            # enables quick & dirty cruft development with emacs
            ('/usr/bin', False),
        )

        import gentoolkit.equery.check
        import gentoolkit.helpers
        import portage
        git_url = 'https://github.com/nilathak/gentoo-root.git'
        gtk_check = gentoolkit.equery.check.VerifyContents()
        gtk_find = gentoolkit.helpers.FileOwner()
        trees = portage.create_trees()
        vardb = trees[portage.settings['EROOT']]["vartree"].dbapi
        vardb_path = os.path.join(portage.settings['EROOT'], portage.const.VDB_PATH)
        
        for repo,baseline_check in repos:
            git_cmd = f'cd {repo} && /usr/bin/git'

            if self.ui.args.rebase:
                self.ui.info(f'Rebasing repo at {repo}...')
                self.dispatch(f'{git_cmd} stash',
                              output='stderr')
                self.dispatch(f'{git_cmd} pull --no-edit',
                              output='both')
                try:
                    self.dispatch(f'{git_cmd} --no-pager stash show',
                                  output=None)
                except self.exc_class:
                    pass
                else:
                    self.dispatch(f'{git_cmd} stash pop',
                                  output='stderr')
            else:
                self.ui.info(f'######################### Checking repo at {repo}...')

                # host branch existing?
                try:
                    self.dispatch(f'{git_cmd} branch | /bin/grep {self.ui.hostname}',
                                  output=None, passive=True)
                except self.exc_class:
                    host_files = list()
                    host_files_diff = list()
                else:
                    # Finding host-specific files (host-only + superimposed)
                    host_files_actual = self.dispatch(f'{git_cmd} diff origin/master {self.ui.hostname} --name-only', output=None, passive=True).stdout
                    # host branches can only modify files from master branch or add new ones
                    host_files = self.dispatch(f'{git_cmd} diff origin/master {self.ui.hostname} --name-only --diff-filter=AM', output=None, passive=True).stdout
                    host_files.sort()
                    host_files_unexpect = set(host_files_actual) - set(host_files)
                    if host_files_unexpect:
                        raise self.exc_class(f'unexpected host-specific diff:{os.linesep}{os.linesep.join(sorted(host_files_unexpect))}')
                    host_files_diff = self.dispatch(f'{git_cmd} diff {self.ui.hostname} --name-only -- {" ".join(host_files)}',
                                                    output=None, passive=True).stdout

                # Finding master-specific files (common files)
                all_files = self.dispatch(f'{git_cmd} ls-files',
                                          output=None, passive=True).stdout
                master_files = list(set(all_files) - set(host_files))
                master_files.sort()
                master_files_diff = self.dispatch(f'{git_cmd} diff origin/master --name-only -- {" ".join(master_files)}',
                                                  output=None, passive=True).stdout

                # display repo status
                if host_files_diff:
                    host_files_stat = self.dispatch(f'{git_cmd} diff {self.ui.hostname} --name-status -- {" ".join(host_files)}',
                                                    output=None, passive=True).stdout
                    self.ui.info(f'Host status:{os.linesep}{os.linesep.join(host_files_stat)}')
                if master_files_diff:
                    master_files_stat = self.dispatch(f'{git_cmd} diff origin/master --name-status -- {" ".join(master_files)}',
                                                      output=None, passive=True).stdout
                    self.ui.info(f'Master status:{os.linesep}{os.linesep.join(master_files_stat)}')

                    # export master changes to avoid checking out master branch instead of host branch
                    # ensure https protocol is used for origin on non-diablo hosts
                    if host_files:
                        clone_path = f'/tmp/{os.path.basename(git_url)}'
                        self.ui.info(f'Preparing temporary master repo for {repo} into {clone_path}...')
                        self.dispatch(f'/bin/rm -rf {clone_path}')
                        self.dispatch(f'{git_cmd} clone {git_url} {clone_path}',
                                      output='stderr')
                        for f in master_files:
                            self.dispatch(f'/bin/cp {os.path.join(repo, f)} {os.path.join(clone_path, f)}',
                                          output='stderr')

                # all portage files in config repo should differ from gentoo baseline, report otherwise (should be deleted from repo manually)
                if baseline_check:
                    self.ui.info(f'Comparing portage files in repo {repo} against baseline...')
                    repo_files = list(set(host_files) | set(master_files))
                    repo_files_abs = [f'/{x}' for x in repo_files]
                    affected_pkgs = {str(x[0]) for x in gtk_find(repo_files_abs)}
                    for pkg in affected_pkgs:
                        check = {k:v for k,v in vardb._dblink(pkg).getcontents().items() if k in repo_files_abs}
                        (n_passed, n_checked, errs) = gtk_check._run_checks(check)
                        if n_passed:
                            for path in set(set(check.keys()) - set(x.split()[0] for x in errs)):
                                self.ui.warning(f'File is equivalent to gentoo baseline: {path}')
                            
                # optionally display repo files
                if self.ui.args.list_files:
                    if host_files:
                        self.ui.info(f'Host files:{os.linesep}{os.linesep.join(host_files)}')
                    if master_files:
                        self.ui.info(f'Master files:{os.linesep}{os.linesep.join(master_files)}')

    @pylon.log_exec_time
    def admin_kernel(self):
        # ====================================================================
        'scripting stuff to build kernels'
        import multiprocessing
        
        key_mp = '/tmp/keyring'
        key_image = '/mnt/work/usb_boot'
        
        os.chdir('/usr/src/linux')
        self.dispatch(f'/usr/bin/make -j{str(multiprocessing.cpu_count()*2-1)}', output='nopipes')

        # install kernel to USB keyrings
        try:
            self.dispatch(f'/bin/mkdir {key_mp}', output='stdout')
        except self.exc_class:
            pass
        try:
            self.dispatch(f'/bin/rm -rf /boot; /bin/ln -s {key_mp}/{self.ui.hostname} /boot', output='stdout')
        except self.exc_class:
            pass

        part = self.dispatch('/sbin/findfs LABEL=KEYRING', passive=True, output=None).stdout[0]
        dev = part[:-1]

        # umount KDE mounts as well, since parallel mounts might lead to ro remounting 
        try:
            while True:
                self.dispatch(f'/bin/umount {part}', passive=True, output='stdout')
        except self.exc_class:
            pass

        self.ui.info('perform automatic offline fsck')
        try:
            self.dispatch(f'/usr/sbin/fsck.vfat -a {part}')
        except self.exc_class:
            pass

        self.dispatch(f'/bin/mount {part} {key_mp}')
        self.dispatch('/usr/bin/make install')

        self.ui.info('install grub modules + embed into boot sector')
        self.dispatch(f'/usr/sbin/grub-install {dev} --boot-directory={key_mp}/boot')
        self.ui.info('rsync new grub installation to keyring backup')
        self.dispatch(f'/usr/bin/rsync -a {key_mp}/boot/grub/ {key_image}/boot/grub/ --exclude="grub.cfg"',
                      output='both')
        self.ui.info('install host-specific grub.cfg (grub detects underlying device and correctly uses absolute paths to kernel images)')
        self.dispatch('/usr/sbin/grub-mkconfig -o /boot/grub/grub.cfg')

        self.ui.info('rsync keyring backup to actual device')
        rsync_exclude = [
            # kernels & grub.cfgs
            '--exclude="/diablo"',
        ]
        rsync_exclude_small = [
            '--include="/systemrescuecd*.iso"',
            '--exclude="/*.iso"',
        ]
        if self.ui.args.small:
            rsync_exclude += rsync_exclude_small 
        dry_run = ''
        if not self.ui.args.force:
            self.ui.info('Use -f to apply sync!')
            dry_run = 'n'
        self.dispatch(f'/usr/bin/rsync -av{dry_run} --modify-window 1 --no-perms --no-owner --no-group --inplace --delete {key_image}/ {key_mp} {" ".join(rsync_exclude)}',
                      output='both')

        try:
            while True:
                self.dispatch(f'/bin/umount {part}', passive=True, output='stdout')
        except self.exc_class:
            pass

        self.dispatch('/bin/rm /boot')
        self.ui.info('Rebuild kernel modules')
        self.dispatch('/usr/bin/make modules_install')
        self.dispatch('/usr/bin/emerge @module-rebuild', output='nopipes')

    def admin_open_vault(self):
        # ====================================================================
        'open encrypted vault'
        from PyQt5.QtWidgets import QApplication, QInputDialog, QLineEdit, QMessageBox
        # application needed before any widget creation
        app = QApplication(sys.argv)

        vault = '/mnt/work/vault'
        mp = '/tmp/vault'

        try:
            os.mkdir(mp)
        except FileExistsError:
            pass
        
        while(True):
            passphrase, ok_event = QInputDialog.getText(None, 'Decryption', 'Passphrase', QLineEdit.Password)
            if not ok_event:
                break
            try:
                self.dispatch(f'echo {passphrase} | gocryptfs -noprealloc {vault} {mp}', output=None)
            except self.exc_class as e:
                QMessageBox.critical(None, 'Error', f'gocryptfs error code {e.owner.ret_val}')
                continue
            try:
                self.dispatch(f'/usr/bin/dolphin {mp}', output=None)
                self.dispatch(f'fusermount -u {mp}', output=None)
            except self.exc_class:
                QMessageBox.critical(None, 'Error', 'displaying folder or unmounting failed')
            finally:
                break
                        
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
            self.dispatch(f'/sbin/btrfs filesystem sync {mount_point}',
                          output=None)
            self.ui.debug('Spinning down...')
            for disk in disks:
                self.dispatch(f'/sbin/hdparm -y {disk}',
                              output=None)

    def admin_update(self):
        # ====================================================================
        'update portage'
        
        import portage
        
        if self.ui.args.sync:
            self.ui.info('Synchronizing repositories...')
            self.dispatch('/usr/sbin/emaint sync -A',
                          output='stderr')
            self.dispatch('/usr/bin/eix-update',
                          output='stderr')

            if self.ui.hostname == 'diablo':
                self.ui.info('Updating KDE repo keyword file links...')
                etc_keywords = '/etc/portage/package.keywords'
                kde_nowarn = '/etc/portage/package.nowarn/kde'
                kde_keywords = '/var/db/repos/kde/Documentation/package.keywords'
                kde_keywords_files = [x.path for x in os.scandir(kde_keywords) if not 'live' in x.name and x.is_file()]
                with open(kde_nowarn, 'w') as kde_nowarn_f:
                    for group in ('kde-applications', 'kde-frameworks', 'kde-plasma'):
                        for old_link in (x.path for x in os.scandir(etc_keywords) if group in x.name):
                            os.remove(old_link)
                        latest_path = sorted(x for x in kde_keywords_files if group in x)[-1]
                        with open(latest_path, 'r') as latest_path_f:
                            for l in latest_path_f:
                                kde_nowarn_f.write(f'{l.rstrip(os.linesep)} in_keywords no_change{os.linesep}')
                        os.symlink(latest_path, os.path.join(etc_keywords, os.path.basename(latest_path)))

        pretend = '-p' if not self.ui.args.force else ''
        options = self.ui.args.options or ''
        self.ui.info('Checking for updates...')
        try:
            self.dispatch(f'/usr/bin/emerge --nospinner -uDNv world {pretend} {options}',
                          output='nopipes')
        except self.exc_class:
            pass
        
        self.ui.info('Checking for obsolete dependencies...')
        self.dispatch(f'/usr/bin/emerge --depclean {pretend}',
                      output='nopipes')

        if self.ui.args.force:
            self.ui.info('Rebuilding broken lib dependencies...')
            self.dispatch('/usr/bin/emerge @preserved-rebuild',
                          output='nopipes')

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
            '--exclude="/var/lib/dhcpcd/"',
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
            self.dispatch(f'/bin/mount | /bin/grep {local}',
                          output=None, passive=True)
        except self.exc_class:
            self.dispatch(f'/bin/mount {image} {local}',
                          output='stderr')
            for (src, dest) in bind_map:
                os.makedirs(src, exist_ok=True)
                self.dispatch(f'/bin/mount -o bind {src} {os.path.join(local, dest.strip("/"))}',
                              output='stderr')
     
        self.ui.info('Entering the chroot...')

        # run batch commands
        opts = ''
        if self.ui.args.options:
            opts = f"c '{self.ui.args.options}'"

        try:
            # chname for chroot hostname modification needs CONFIG_UTS_NS=y
            self.dispatch(f'/usr/bin/env -i HOME=$HOME $HOSTNAME={device} TERM=$TERM /usr/bin/chname {device} /usr/bin/linux32 /usr/bin/chroot {local} /bin/bash -l{opts}',
                          output='nopipes')
        except self.exc_class:
            self.ui.warning('chroot shell exited with error status (last cmd failed?)')
        self.ui.info('Leaving the chroot...')
     
        # last instance does umounting
        if len([x for x in self.dispatch('/bin/ps aux | /bin/grep admin.py',
                                         output=None,
                                         passive=True).stdout if ' wrap' in x]) == 1:
            for (src, dest) in reversed(bind_map):
                self.dispatch(f'/bin/umount {os.path.join(local, dest.strip("/"))}',
                              output='stderr')
     
            try:
                if self.ui.args.sync:
                    self.ui.info('Syncing changes to device...')
                    try:
                        self.dispatch(f'/bin/ping {device} -c 1',
                                      output='stderr')
                        try:
                            self.dispatch(f'/usr/bin/rsync -aHv --delete {local}/ {device}:/ {" ".join(rsync_exclude)}',
                                          output='both')
                            self.ui.info('Updating grub in native environment...')
                            self.dispatch(f'/usr/bin/ssh {device} /usr/sbin/grub-install /dev/sda',
                                          output='both')
                            self.dispatch(f'/usr/bin/ssh {device} /usr/sbin/grub-mkconfig -o /boot/grub/grub.cfg',
                                          output='both')
                        except self.exc_class:
                            self.ui.warning('Something went wrong during the rsync process...')
                    except self.exc_class:
                        self.ui.warning('Device is offline, changes are NOT synced...')
            finally:
                self.dispatch(f'/usr/bin/sleep 0.2 && /bin/umount {local}',
                              output='stderr')
        else:
            self.ui.warning('No other device chroot environment should be open while doing rsync, close them...')

if __name__ == '__main__':
    app = admin(job_class=pylon.gentoo.job.job,
                ui_class=ui)
    app.run()

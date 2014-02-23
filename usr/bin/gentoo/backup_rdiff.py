# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
# GENERAL TIPS n' TRICKS
#  - NEVER write to the mirror directory
#
# RESTORING TIPS n' TRICKS
#  - use google archfs for convenient access to incremental backups
#  - rdiff-backup options:
#       -r, --restore-as-of restore_time
#              Restore  the  specified directory as it was as of restore_time.  See the TIME FORMATS section for more information on the format of restore_time,
#              and see the RESTORING section for more information on restoring.
#       --list-at-time time
#              List  the  files  in the archive that were present at the given time.  If a directory in the archive is specified, list only the files under that
#              directory.
#       --list-changed-since time
#              List the files that have changed in the destination directory since the given time.  See TIME FORMATS for the format of time.  If a directory  in
#              the  archive  is  specified, list only the files under that directory.  This option does not read the source directory; it is used to compare the
#              contents of two different rdiff-backup sessions.
# ====================================================================
import datetime
import os
import re
import pylon.base

output_pat = re.compile('(.*)\s+([0-9]+[.]*[0-9]*)\s+(\w+)\s+[0-9]+.*')
remove_older_limit      = 2.0
remove_hysteresis_limit = 1.7
diff_date_format   = '%a %b %d %H:%M:%S %Y'

class backup_rdiff(pylon.base.base):
    'implement backups using rdiff-backup'

    @pylon.base.memoize
    def determine_obsolete_diff_sets(self, dest_path, opts=''):
        'determine if remove_older_limit has been reached'

        # get size statistics of rdiff mirror
        output = self.dispatch('rdiff-backup ' +

                               # get increment size
                               '--list-increment-sizes ' +

                               # add additional options
                               opts + ' ' +

                               dest_path,
                               output=None).stdout

        # extract date and size column from output
        diff_sizes = {}
        # ignore the two header lines
        if not self.ui.opts.dry_run:
            for l in output[2:]:
                m = re.match(output_pat, l)
                if m:
                    # save diff set date as datetime object
                    date_obj = datetime.datetime.strptime(m.group(1).rstrip(), diff_date_format)

                    # normalize MB, GB and TB units
                    diff_sizes[date_obj] = self.normalize_size(float(m.group(2)), m.group(3))

        # get nice list of diff-set dates
        ascending_date_keys = sorted(diff_sizes.keys(), reverse= True)

        # determine starting point from which to delete older diff sets
        cumulative_limit = 0
        obsolete_diff_key = None
        for k in ascending_date_keys:
            cumulative_limit += diff_sizes[k]
            if cumulative_limit / diff_sizes[ascending_date_keys[0]] > remove_older_limit:
                obsolete_diff_key = k
                break

        # determine starting point from which to actually delete older
        # diff sets (including a defined hysteresis)
        cumulative_limit = 0
        oldest_retained_key = None
        previous_key = ascending_date_keys[0]
        for k in ascending_date_keys:
            cumulative_limit += diff_sizes[k]
            if cumulative_limit / diff_sizes[ascending_date_keys[0]] > remove_hysteresis_limit:
                oldest_retained_key = previous_key
                break
            previous_key = k

        if obsolete_diff_key:
            s = []
            for k in ascending_date_keys:
                norm_str = '%s = %15.2f' % (k.strftime(diff_date_format), diff_sizes[k])
                if k < oldest_retained_key:
                    norm_str += ' <- scheduled for deletion'
                s.append(norm_str)
            self.ui.info('Diff sets:' + os.linesep + os.linesep.join(s))

            return oldest_retained_key.strftime(diff_date_format)
        return None

    def verify(self, dest_path, opts=''):
        self.ui.info('Verifying metadata of %s...' % dest_path)
        self.dispatch('rdiff-backup ' +

                      # check for metadata consistency
                      '--verify ' +

                      # add additional options
                      opts + ' ' +

                      dest_path,
                      output='both')

    def do(self, src_path, dest_path, opts=''):

        self.ui.info('Saving %s to %s...' % (src_path, dest_path))
        self.dispatch('rdiff-backup ' +
         
                      # Exclude files on file systems (identified by
                      # device number) other than the file system the
                      # root of the source directory is on.
                      '--exclude-other-filesystems ' +

                      # Exclude socket files to avoid related errors.
                      # Even without this option rdiff-backup finishes
                      # successfully and does restore the sockets
                      # mentioned in the error messages with help of
                      # the stored metadata.
                      '--exclude-sockets ' +

                      # This option prevents rdiff-backup from flagging a hardlinked file as changed when its device number and/or inode changes.  This option is useful  in
                      # situations  where  the  source  filesystem lacks persistent device and/or inode numbering.  For example, network filesystems may have mount-to-mount
                      # differences in their device number (but possibly stable inode numbers); USB/1394 devices may come up at different device numbers each  remount  (but
                      # would generally have same inode number); and there are filesystems which don't even have the same inode numbers from use to use.  Without the option
                      # rdiff-backup may generate unnecessary numbers of tiny diff files.
                      '--no-compare-inode ' +
         
                      # mirrored hardlinks over nfs lead to missing SHA1 digests.
                      # however the link information is preserved in rdiff-backup's
                      # metadata. restoring will recreate the hardlinks
                      '--no-hard-links ' +

                      # create all parent dirs
                      '--create-full-path ' +

                      # add additional options
                      opts + ' ' +
         
                      src_path + ' ' + dest_path,
                      output='both')

        self.verify(dest_path, opts)

        # determine diff sets to remove
        obsolete_diff_date = self.determine_obsolete_diff_sets(dest_path, opts)
        if obsolete_diff_date:
            self.ui.warning('Backup of %s has crossed size limit of %.2f! Use modify -o"remove,%s"' % (dest_path,
                                                                                                       remove_older_limit,
                                                                                                       obsolete_diff_date))

    def info(self, src_path, dest_path, opts=''):

        # determine diff sets to remove
        obsolete_diff_date = self.determine_obsolete_diff_sets(dest_path, opts)
        if obsolete_diff_date:
            self.ui.warning('Backup of %s has crossed size limit of %.2f! Use modify -o"remove,%s"' % (dest_path,
                                                                                                       remove_older_limit,
                                                                                                       obsolete_diff_date))

        # diff src to dest
        self.ui.info('Differences %s <-> %s...' % (src_path, dest_path))
        try:
            self.dispatch('rdiff-backup ' +
         
                          '--exclude-other-filesystems ' +
                          '--exclude-sockets ' +
                          '--no-compare-inode ' +
                          '--no-hard-links ' +


                          # Compare a directory with the backup set at the
                          # given time. Regular files will be compared by
                          # computing their SHA1 digest on  the  source  side  and
                          # comparing it to the digest recorded in the
                          # metadata.
                          # Since we assume the metadata is consistent
                          # with the backup dest we use this instead of --compare-at-time
                          '--compare-hash-at-time now ' +

                          # add additional options
                          opts + ' ' +

                          src_path + ' ' + dest_path,
                          output='both')
        except self.exc_class:
            self.ui.warning('The backup at %s is not up-to-date!' % dest_path)

    def modify(self, src_path, dest_path, opts=''):
        'modify backup destinations according to -o switch'

        cmd = self.ui.opts.options.split(',')[0]
        arg = self.ui.opts.options.split(',')[1]

        if cmd == 'remove':
            self.ui.info('Removing obsolete diff sets from %s...' % dest_path)
            self.dispatch('rdiff-backup ' +

                          # use permanent force
                          '--force --remove-older-than "' + arg + '" ' +

                          # add additional options
                          opts + ' ' +
         
                          dest_path,
                          output='both')

    @staticmethod
    def normalize_size(size, factor):
        if factor == 'MB':
            return size * 1024
        elif factor == 'GB':
            return size * 1024 * 1024
        elif factor == 'TB':
            return size * 1024 * 1024 * 1024
        return size


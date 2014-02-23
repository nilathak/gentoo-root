# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
# FIXME
# - enable mail reporting in cleanup method only for >daily backups
# - check if link-dest is done the right way during startup (do not duplicate)
# - implement ratio dependant deletion of rsync steps (not the
#   coarsest, but also not the finest). use the step with the smallest
#   real size (du?) between those two
# - rsync error codes which might be treated as warnings
#    23 and 24 are treated as warnings because users might be using the filesystem during the backup
#    if you want perfect backups, don't allow the source to be
#    modified while the backups are running :)
# - roll back when using --link-dest? does this use up more space over
#   time?
# ====================================================================
import datetime
import os
import pylon.base

class backup_snapshot(pylon.base.base):
    'implement "tower of hanoi" snapshot backups using rsync and its --link-dest option.'

    # describe towers of hanoi rotation steps
    # Q                                                                                Q
    # M                          M                          M
    # W        W        W                 W        W                 W        W
    # D  D  D     D  D     D  D     D  D     D  D     D  D     D  D     D  D     D  D
    # HHH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH HH
    rsync_deltas = {
        #'quarterly.1': datetime.timedelta(weeks=13),
        #'monthly.1':   datetime.timedelta(days=30),
        #'weekly.3':    datetime.timedelta(weeks=3),
        #'weekly.1':    datetime.timedelta(weeks=1),
        #'daily.6':     datetime.timedelta(days=6),
        #'daily.5':     datetime.timedelta(days=5),
        #'daily.4':     datetime.timedelta(days=4),
        #'daily.3':     datetime.timedelta(days=3),
        #'daily.2':     datetime.timedelta(days=2),
        #'daily.1':     datetime.timedelta(days=1),
        #'hourly.8':    datetime.timedelta(hours=8),
        #'hourly.4':    datetime.timedelta(hours=4),
        #'hourly.2':    datetime.timedelta(hours=2),
        #'hourly.1':    datetime.timedelta(hours=1),
        'minutely.4':   datetime.timedelta(minutes=4),
        'minutely.3':   datetime.timedelta(minutes=3),
        'minutely.2':   datetime.timedelta(minutes=2),
        'minutely.1':   datetime.timedelta(minutes=1),
        'secondly.24':  datetime.timedelta(seconds=24),
        'secondly.12':  datetime.timedelta(seconds=12),
        'secondly.6':   datetime.timedelta(seconds=6),
        'secondly.3':   datetime.timedelta(seconds=3),
        }
    rsync_deltas_sorted = sorted(rsync_deltas.keys(), key=rsync_deltas.get)
    rsync_metadata_str  = 'metadata'
    rsync_snapshot_str  = 'snapshot.'
    rsync_timestamp_str = 'timestamp.'

    @pylon.base.memoize
    def initialize_paths(self, dest_path):
        # create metadata directory if not already existing
        metadata_path = os.path.join(dest_path, backup_snapshot.rsync_metadata_str)
        if not os.path.exists(metadata_path):
            os.makedirs(metadata_path)

        # construct paths to snapshot dirs
        snapshot_path = os.path.join(dest_path, backup_snapshot.rsync_snapshot_str)
        paths = dict([(k, snapshot_path + k) for k in backup_snapshot.rsync_deltas.keys()])

        # check for existing snapshot dirs and initialize
        # directory mtimes and metadata if even a single one is missing
        not_existing_keys = [k for k in paths.keys() if not os.path.exists(paths[k])]
        if len(not_existing_keys) > 0:
            for k in not_existing_keys:
                os.mkdir(paths[k])

            # mark all snapshots as just created (but at least with
            # some degree of order for nice timestamp results)
            for k in backup_snapshot.rsync_deltas_sorted:
                self.set_utime(paths[k],
                               datetime.datetime.today() -
                               datetime.timedelta(seconds=backup_snapshot.rsync_deltas_sorted.index(k)))

            # mark finest interval as just overdue. this ensures we're
            # able to bootstrap into a snapshot config right away
            finest_delta_key = backup_snapshot.rsync_deltas_sorted[0]
            self.set_utime(paths[finest_delta_key],
                      datetime.datetime.today() -
                      backup_snapshot.rsync_deltas[finest_delta_key] -
                      datetime.timedelta(seconds=1))
        return paths

    @pylon.base.memoize
    def initialize_timestamps(self, dest_path):

        paths = self.initialize_paths(dest_path)

        # create initial timestamps
        metadata_path = os.path.join(dest_path, backup_snapshot.rsync_metadata_str)
        timestamp_paths = dict([(k, os.path.join(metadata_path, backup_snapshot.rsync_timestamp_str + k)) for k in paths.keys()])
        not_existing_timestamp_keys = [k for k in timestamp_paths.keys() if not os.path.exists(timestamp_paths[k])]
        for k in not_existing_timestamp_keys:
            os.mkdir(timestamp_paths[k])
            self.set_utime(timestamp_paths[k], self.get_mtime(paths[k]))
        return timestamp_paths

    def do(self, src_path, dest_path, opts=''):
        paths = self.initialize_paths(dest_path)
        timestamp_paths = self.initialize_timestamps(dest_path)

        # ===================================
        # hanoi testcase construction
        # ===================================

        s = backup_snapshot.rsync_deltas_sorted

        # set all snapshots to 1 minute away from being overdue
        state_time = dict([(k, datetime.datetime.today() - backup_snapshot.rsync_deltas[k] + datetime.timedelta(seconds=1)) for k in s])

        # avoid race condition logic to kick in (see code below for that)
        state_time[s[0]] += datetime.timedelta(seconds=1)
        for k in s[0:-1]:
            print(k)
            state_time[s[s.index(k)+1]] += backup_snapshot.rsync_deltas[k]

        # set some overdue snapshots
        #state_time['hourly.8']    -= backup_snapshot.rsync_deltas['hourly.4'] + datetime.timedelta(seconds=2)
        #state_time['daily.1']     -= datetime.timedelta(seconds=2)

        # set utimes
        #state_time_keys = state_time.keys()
        #state_time_keys = ['weekly.1', 'hourly.2', 'daily.1']

        # FIXME comment this out for productive use!
        #map(lambda k: self.set_utime(paths[k], state_time[k]), state_time_keys)
        # ===================================
        # ===================================

        mtime = dict([(k, self.get_mtime(paths[k])) for k in paths.keys()])

        # find overdue snapshot dirs
        overdue_keys = [k for k in mtime.keys() if (datetime.datetime.today() - mtime[k]) > backup_snapshot.rsync_deltas[k]]
        if len(overdue_keys) < 1:
            raise self.exc_class('No overdue snapshots found for ' + src_path)

        # the coarsest snapshot interval that is overdue shall be used
        # as rsync destination
        diff_key = sorted(overdue_keys, key=backup_snapshot.rsync_deltas.get)[-1]

        # check for a race condition between two adjacent snapshot
        # intervals
        try:
            # the larger timedelta will be at idx + 1
            larger_delta_key = backup_snapshot.rsync_deltas_sorted[backup_snapshot.rsync_deltas_sorted.index(diff_key) + 1]
        except Exception:
            pass
        else:
            # check if a currently overdue, small timedelta will be
            # followed by an overdue larger timedelta in the near
            # future. if the larger timedelta is overdue anyway we
            # cannot afford to lose the backup span of the smaller
            # timedelta.
            if ((datetime.datetime.today() - mtime[larger_delta_key]) >
                (backup_snapshot.rsync_deltas[larger_delta_key] - backup_snapshot.rsync_deltas[diff_key])):
                diff_key = larger_delta_key

        # set diff_path now, after diff_key is determined
        diff_path = paths[diff_key]

        # the finest snapshot interval available is always used as
        # diff target
        diff_key_idx = backup_snapshot.rsync_deltas_sorted.index(diff_key)
        if diff_key_idx > 0:
            full_key = backup_snapshot.rsync_deltas_sorted[diff_key_idx - 1]
        else:
            full_key = diff_key
        full_path = paths[full_key]

        self.ui.info('Saving %s to %s snapshot (linked to %s)...' % (src_path, diff_key, full_key))
        self.dispatch('rsync ' +
                      # preserve almost everything
                      '--archive ' +

                      # preserve hard links too
                      '--hard-links ' +

                      # preserve sparse files
                      '--sparse ' +

                      # NFS backup destination benefits from this
                      '--omit-dir-times ' +

                      # prevent deadlocks
                      '--timeout=10 ' +

                      # reuse partially transmitted files (eg after
                      # user break)
                      '--partial ' +

                      # delete unwanted files on destination
                      '--delete-during ' +

                      # remove empty dirs on destination
                      #'--prune-empty-dirs ' +

                      # always link to latest snapshot. older
                      # snapshots will exhibit more disk usage over time.
                      '--link-dest=' + full_path + ' ' +

                      # add additional rsync options
                      opts + ' ' +

                      # paths
                      src_path + ' ' + diff_path,
                      output='both')

        # the overdue snapshots and all smaller timedeltas are affected
        affected_snapshot_keys = backup_snapshot.rsync_deltas_sorted[0:backup_snapshot.rsync_deltas_sorted.index(diff_key)]

        if not self.ui.opts.dry_run:

            for k in affected_snapshot_keys:
                # before losing information about the age of smaller
                # snapshots, save it to a timestamp file (for info)
                self.set_utime(timestamp_paths[k], mtime[k])

                # update mtime of all smaller timedeltas (hanoi scheme).
                self.set_utime(paths[k], datetime.datetime.today())

            # finally adjust mtime & timestamp of the overdue snapshot
            self.set_utime(paths[diff_key], datetime.datetime.today())
            self.set_utime(timestamp_paths[diff_key], datetime.datetime.today())

    def info(self, src_path, dest_path, opts=''):
        paths = self.initialize_paths(dest_path)
        timestamp_paths = self.initialize_timestamps(dest_path)

        sorted_timestamp_keys = sorted(timestamp_paths.keys(),
                                       key=lambda k: self.get_mtime(timestamp_paths[k]))

        timestamp_prefix = dict([(k, ''.center(sorted_timestamp_keys.index(k) * 2, ' ')) for k in timestamp_paths.keys()])

        # display in definition order
        timeline_str = 'Snapshot timeline for ' + dest_path + ':'
        for k in backup_snapshot.rsync_deltas_sorted:
            timeline_str += os.linesep + '' + timestamp_prefix[k] + '|' + k

        self.ui.info(timeline_str)

        # display the backup sizes using du
        snapshot_path = os.path.join(dest_path, backup_snapshot.rsync_snapshot_str)
        self.ui.info('Snapshot sizes (hard link rot) for ' + dest_path + ':' + os.linesep +
                     os.linesep.join(self.dispatch('du -s ' + snapshot_path + '*',
                                                   output=None).stdout))

    def modify(self, src_path, dest_path, opts=''):
        pass

    @staticmethod
    def get_mtime(path):
        return datetime.datetime.fromtimestamp(os.path.getmtime(path))

    @staticmethod
    def set_utime(path, datetime):
        import time
        os.utime(path, (time.mktime(datetime.timetuple()),
                        time.mktime(datetime.timetuple())))

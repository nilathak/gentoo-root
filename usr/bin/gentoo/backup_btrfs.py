# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
# NOTES
# - if !newest timedelta slot contains 0 timestamps => nothing
# - if  newest timedelta slot contains 0 timestamps => take snapshot
# - if !oldest timedelta slot contains more than 1 timestamp => keep oldest, delete others
# - if  oldest timedelta slot contains more than 1 timestamp => keep newest, delete others
# - if no timedeltas are specified, all new snapshots from src are cloned to dest, obsolete ones in dest are deleted
# - deleting snapshot will always happen together with creating snapshots, since older deltas are always >= newer deltas 
# - the single snapshot after the oldest timedelta is kept for/replaced after oldest-1 timedelta
# - Taking snapshots of a subvolume is not a recursive process. If a snapshot of a subvolume is created,
#   every subvolume or snapshot that the subvolume already contains is mapped to an empty directory of the
#   same name inside the snapshot (https://en.wikipedia.org/wiki/Btrfs#Subvolumes_and_snapshots)
# - Remote backup with send/receive are faster than simple rsync
# - source snapshot paths must always be specified as absolute paths from root, not path to mount point,
#   or otherwise the automatic extraction of incremental snapshot directory fails
# - an empty options string represents a cloning command. if src & dest are on same fs, then it just represents one large timedelta
# - determining the sizes of specific snapshots (via quota & qgroup) is usually meaningless. deleting snapshots from within the
#   timedelta grid simply shifts shared data to the neighboring snapshots. reduction in size can only be reached by simply
#   deleting the oldest snapshots (which can easily done manually without any adm_backup operation).
#   it would only make sense for snapshots which show a large "exclusive size" (3rd column in qgroup output), which can be elevated
#   for snapshots containing many large transient files (downloads, caches, ...), but it's generally better to decrease
#   snapshot retention time in this case
#
# POOL -> EXTPOOL
# =======================
#        OK, call the sending system "S" and the receiving system "R". Let's
#  say we've got three subvolumes on S:
#   
#  S:A2, the current /home (say)
#  S:A1, a snapshot of an earlier version of S:A2
#  S:B, a separate subvolume that's had some CoW copies of files in both
#       S:A1 and S:A2 made into it.
#   
#     If we send S:A1 to R, then we'll have to send the whole thing,
#  because R doesn't have any subvolumes yet.
#   
#     If we now want to send S:A2 to R, then we can use -p S:A1, and it
#  will send just the differences between those two. This means that the
#  send stream can potentially ignore a load of the metadata as well as
#  the data. It's effectively saying, "you can clone R:A1, then do these
#  things to it to get R:A2".
#   
#     If we now want to send S:B to R, then we can use -c S:A1 -c S:A2.
#  Note that S:B doesn't have any metadata in common with either of the
#  As, only data. This will send all of the metadata ("start with an
#  empty subvolume and do these things to it to get R:B"), but because
#  it's known to share data with some subvols on S, and those subvols
#  also exist on R, we can avoid sending that data again by simply
#  specifying where the data can be found and reflinked from on R.
#   
#     So, if you have a load of snapshots, you can do one of two things
#  to duplicate all of them:
#   
#  btrfs sub send <snap 0>
#  for n=1 to N
#     btrfs sub send -p <snap n-1> <snap n>
#   
#     Or, in any order,
#   
#  btrfs sub send <snap s1>
#  for n=1 to N
#     btrfs sub send -c <snap s1> -c <snap s2> -c <snap s3> ... <snap sn>
#   
#  where each subvolume that's been sent before gets added as a -c to the
#  next send command. This second approach means that all possible
#  reflinks between subvolumes can be captured, but it will send all of
#  the metadata across each time. The first approach may lose some manual
#  reflink efficiency, but is better at sending only the necessary
#  changed metadata. You should be able to combine the two methods, I
#  think.
#   
#     I'm trying to think of a case where -c is useful that doesn't
#  involve someone having done cp --reflink=always between subvolumes,
#  but I can't. So, I think the summary is:
#   
#   * Use -p to deal with parent-child reflinks through snapshots
#   * Use -c to specify other subvolumes (present on both sides) that
#      might contain reflinked data 
# OK, you can use -c if you don't have a record of the relationships between the subvolumes you want to send,
# but know that they're related in some way. As above, you send the first subvol "bare", and then
# supply a -c for each one that you've already sent. It just allows the sending side to tell the receiving side that
# there's some shared data in use that it's already got the data for, and it just needs to hook up the extents.
# ====================================================================

import datetime
import functools
import os
import re
import pylon.base

snapshot_pattern = '%Y-%m-%dT%H-%M-%S'
snapshot_regex = '[0-9]*-[0-9]*-[0-9]*T[0-9]*-[0-9]*-[0-9]*'

class backup_btrfs(pylon.base.base):
    'implement btrfs subvolume snapshot backups based on interval string'
    
    @classmethod
    def unique_logspace(cls, data_points, interval_range):
        import math
        exp = [x * math.log(interval_range)/data_points for x in range(0, data_points)]
        logspace = [int(round(math.exp(x))) for x in exp]
        for idx,val in enumerate(logspace):
            if idx > 0:
                if val <= new_val:
                    new_val = new_val + 1
                else:
                    new_val = val
            else:
                new_val = val
            yield new_val
                
    @classmethod
    def get_ts_of_path(cls, path):
        return datetime.datetime.strptime(re.search(snapshot_regex, path).group(0), snapshot_pattern)
            
    @classmethod
    def get_path_of_ts(cls, path, name, ts):
        return os.path.join(path, name + '.' + ts.strftime(snapshot_pattern))
    
    @classmethod
    def get_ts_now(cls):
        return datetime.datetime.today().replace(microsecond=0)
    
    def get_td(self, delta_str):
        if 'h' in delta_str:
            num = int(re.search('([0-9]*)h', delta_str).group(1))
            for delta in self.unique_logspace(num, 24):
                # reduce hour delta to better match backup script calling resolution (cron.hourly)
                # this ensures we're taking a snapshot each hour
                yield datetime.timedelta(minutes=60*delta-1, seconds=55)
        if 'd' in delta_str:
            num = int(re.search('([0-9]*)d', delta_str).group(1))
            for delta in self.unique_logspace(num, 30):
                yield datetime.timedelta(days=delta)
        if 'm' in delta_str:
            num = int(re.search('([0-9]*)m', delta_str).group(1))
            for delta in self.unique_logspace(num, 12):
                yield datetime.timedelta(days=delta*30)
        if 'y' in delta_str:
            num = int(re.search('([0-9]*)y', delta_str).group(1))
            for delta in self.unique_logspace(num, 1):
                yield datetime.timedelta(days=delta*365)
                
        # - append delta to max past, to facilitate keeping 1 snapshot after last configured delta
        # - subtract 1 min to avoid datetime overflows in window calculations
        yield self.get_ts_now() - datetime.datetime.min - datetime.timedelta(minutes=1)

    def get_ts(self, path):
        import glob
        for d in glob.glob(path):
            try:
                yield self.get_ts_of_path(d)
            except Exception:
                self.ui.warning('Failed to extract ts: ' + d)

    def do(self, src_path, dest_path, opts=''):

        # FIXME this still occured !!!!!!
        # Thread-1: ### adm_backup(2015-04-15 00:56:44,325) ERROR: <class 'pylon.base.script_error'> uuid extraction failed
        # use random thread startup times for
        # - avoiding errors during multithreaded "btrfs filesystem show"
        # - better readability
        import random
        import time
        time.sleep(random.random())

        self.ui.info('Saving {0} to {1}...'.format(src_path, dest_path))

        # check if src is really a btrfs subvolume
        try:
            self.dispatch('btrfs subvolume show ' + src_path,
                          passive=True, output=None)
        except self.exc_class:
            raise self.exc_class('source {0} needs to be a valid btrfs subvolume'.format(src_path))
        
        send_dir, name = os.path.split(src_path)
        recv_dir = dest_path

        # determine if we're about to send snapshots between two btrfs instances
        uuid = []
        for d in (send_dir, recv_dir):
            output = self.dispatch('btrfs filesystem show {0}'.format(d),
                                   passive=True,
                                   output=None).stdout
            try:
                uuid.append(re.search('uuid: (.*)', output[0]).group(1))
            except Exception:
                raise self.exc_class('uuid extraction failed')
        same_fs = uuid[0] == uuid[1]
        
        ts_now = self.get_ts_now()
        td_list = list(self.get_td(opts))
        # assume one large timedelta for same_fs
        cloning = len(td_list) < 2 and not same_fs

        # distinguish snapshot sets by hash (so timedeltas are not applied to send/receive references of other dests)
        import hashlib
        # cloning does not create new hashes, but rather looks at existing ones
        hash_dir = send_dir if cloning else recv_dir
        hash = hashlib.md5(hash_dir.encode('utf-8')).hexdigest()
        hash_glob = '*' + hash + '*'
        
        send_path = self.get_path_of_ts(send_dir, name + '.' + hash, ts_now)
        recv_path = self.get_path_of_ts(recv_dir, name + '.' + hash, ts_now)
        ts_send_list = sorted(list(self.get_ts(os.path.join(send_dir, name) + hash_glob)))
        ts_recv_list = sorted(list(self.get_ts(os.path.join(recv_dir, name) + hash_glob)))
        # cloning does not create new hashes, but rather looks at existing ones
        ts_list = ts_send_list if cloning else ts_recv_list
        
        ## TODO testcase
        ## ==========================
        #src_test_ts = [
        #    #ts_now - datetime.timedelta(hours=10),
        #    #ts_now - datetime.timedelta(hours=15),
        #    #ts_now - datetime.timedelta(days=302),
        #]
        #src_test_paths = list(map(lambda x: self.get_path_of_ts(send_dir, name + '.' + hash, x), src_test_ts))
        #dest_test_ts = [
        #    #ts_now - datetime.timedelta(minutes=30),
        #    #ts_now - datetime.timedelta(hours=5),
        #    #ts_now - datetime.timedelta(hours=6),
        #    #ts_now - datetime.timedelta(hours=11),
        #    #ts_now - datetime.timedelta(hours=12),
        #    #ts_now - datetime.timedelta(hours=13),
        #    #ts_now - datetime.timedelta(hours=14),
        #    #ts_now - datetime.timedelta(hours=19),
        #    #ts_now - datetime.timedelta(hours=20),
        #    #ts_now - datetime.timedelta(days=20),
        #    #ts_now - datetime.timedelta(days=40),
        #    #ts_now - datetime.timedelta(days=200),
        #    #ts_now - datetime.timedelta(days=300),
        #    #ts_now - datetime.timedelta(days=400),
        #    #ts_now - datetime.timedelta(days=401),
        #]
        #dest_test_paths = list(map(lambda x: self.get_path_of_ts(recv_dir, name + '.' + hash, x), dest_test_ts))
        ## add reference snapshots
        #if recv_dir != send_dir:
        #    pass
        #    #src_test_paths.append(dest_test_paths[0].replace(recv_dir, send_dir))
        #    #src_test_paths.append(dest_test_paths[1].replace(recv_dir, send_dir))
        # 
        #try:
        #    [os.mkdir(t) for t in src_test_paths]
        #    [os.mkdir(t) for t in dest_test_paths]
        ## ==========================
        
        for idx,td in enumerate(td_list):

            # how many snapshots are within the current timedelta window?
            ts_within_td = []

            # the newest timedelta window starts at now - 0
            if idx == 0:
                td_prev = datetime.timedelta()

            self.ui.debug('Checking delta: ' + str(td))
            for ts in ts_list:
                if (ts_now - td < ts) and (ts_now - td_prev > ts):
                    ts_within_td.append(ts)

            td_prev = td

            if idx == 0:

                if not ts_within_td:
                    snap_path = recv_path if same_fs else send_path
                    self.ui.info('Taking snapshot {0}...'.format(snap_path))
                    self.dispatch('btrfs subvolume snapshot -r {0} {1}'.format(src_path,
                                                                               snap_path))
                    ts_send_list.append(ts_now)
                    ts_within_td.append(ts_now)

                if cloning or not same_fs:

                    ts_of_clones    = sorted(list(set(ts_send_list) & set(ts_recv_list)))
                    ts_to_clone     = sorted(list(set(ts_send_list) - set(ts_recv_list)))
                    ts_to_delete    = sorted(list(set(ts_recv_list) - set(ts_send_list)))

                    # clone all new timestamps
                    for ts in ts_to_clone:

                        # assemble string of clones timestamp paths
                        clone_str = ''
                        for clone in ts_of_clones:
                            clone_str = clone_str + ' -c ' + self.get_path_of_ts(send_dir, name + '.' + hash, clone)

                        # transfer reference snapshot and any reflink relations
                        self.ui.info('Cloning to {0}...'.format(recv_path))
                        self.dispatch('btrfs send {0} {1} | btrfs receive {2}'.format(clone_str,
                                                                                      self.get_path_of_ts(send_dir, name + '.' + hash, ts),
                                                                                      recv_dir),
                                      output='stderr')

                        # add freshly cloned snapshot as new clone
                        ts_of_clones.append(ts)

                    # cleanup obsolete timestamps
                    if cloning:
                        # use deletion code at end of td loop
                        ts_within_td = ts_to_delete
                        ts_within_td.append(ts_of_clones[0])
                    else:
                        # deleting obsolete reference snapshots on src
                        for ts in ts_of_clones[:-1]:
                            path = self.get_path_of_ts(send_dir, name + '.' + hash, ts)
                            self.ui.info('Deleting obsolete reference: ' + path)
                            self.dispatch('btrfs subvolume delete -c ' + path)

            # keep only the newest snapshot in the oldest timedelta window
            if (idx == len(td_list) - 1 or
                
                # - we need to keep newest snapshot in newest timedelta for
                #   incremental send/receive case
                # - a reference snapshot on src is only available for the newest timedelta
                # - this condition surfaces when going from small to large timedelta resolution
                idx == 0 and not same_fs):
                ts_within_td = reversed(ts_within_td)

            for idx,ts in enumerate(ts_within_td):
                path = self.get_path_of_ts(recv_dir, name + '.' + hash, ts)
                if idx > 0:
                    self.ui.info('Deleting snapshot: ' + path)
                    self.dispatch('btrfs subvolume delete -c ' + path)
                else:
                    self.ui.debug('Keeping snapshot: ' + path)

        self.ui.info('Saved {0} to {1}'.format(src_path, dest_path))
        #finally:
        #    [os.rmdir(t) for t in src_test_paths]
        #    [os.rmdir(t) for t in dest_test_paths]
            
    def info(self, src_path, dest_path, opts=''):
        pass

    def modify(self, src_path, dest_path, opts=''):
        pass

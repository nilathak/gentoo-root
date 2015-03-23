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
# - if no timedeltas are specified, a snapshot is created every time the script is called, and only the newest is kept
# - deleting snapshot will always happen together with creating snapshots, since older deltas are always >= newer deltas 
# - the single snapshot after the oldest timedelta is kept for/replaced after oldest-1 timedelta
# - Taking snapshots of a subvolume is not a recursive process. If a snapshot of a subvolume is created,
#   every subvolume or snapshot that the subvolume already contains is mapped to an empty directory of the
#   same name inside the snapshot (https://en.wikipedia.org/wiki/Btrfs#Subvolumes_and_snapshots)
# - Remote backup with send/receive are faster than simple rsync
#
# TODO
# - BACKUP LUKSHEADERs!!!
# - when booting from pool to shift root of cache into diablo subvolume, DONT FORGET TO ENABLE SLIM-METADATA feature on cache (enable on pool after switching back to cache)
# - use btrfs sync after each subvolume snapshot command to ensure the snapshot has been written to disk (especially after removal of snapshots!)
# - add hashlib.md5('asdf').hexdigest() to snapshot names
# - determine same_root by trying btrfs sub snap
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
    'implement btrfs snapshot backups based on interval string'
    
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
        return datetime.datetime.strptime(re.search(snapshot_regex, path), snapshot_pattern)
            
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
            except ValueError:
                self.ui.warning('Failed to extract ts: ' + d)

    def do(self, src_path, dest_path, opts=''):

        ts_now = self.get_ts_now()
        send_dir, name = os.path.split(src_path)
        recv_dir = dest_path
        send_path = self.get_path_of_ts(send_dir, name, ts_now)
        recv_path = self.get_path_of_ts(recv_dir, name, ts_now)

        # check if src is really a btrfs subvolume (preferably a btrfs root)
        try:
            self.dispatch('btrfs subvolume show ' + src_path,
                          passive=True, output=None)
        except self.exc_class:
            raise self.exc_class('source {0} needs to be a valid btrfs subvolume'.format(src_path))
        
        ## FIXME testcase
        ## ==========================
        #src_test_paths = []
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
        #    ts_now - datetime.timedelta(days=40),
        #    ts_now - datetime.timedelta(days=200),
        #    ts_now - datetime.timedelta(days=300),
        #    ts_now - datetime.timedelta(days=400),
        #    ts_now - datetime.timedelta(days=401),
        #    ]
        #dest_test_paths = list(map(lambda x: self.get_path_of_ts(recv_dir, name, x), dest_test_ts))
        ## add reference snapshots
        #dest_test_paths.append(dest_test_paths[0] + '.ro') 
        ##dest_test_paths.append(dest_test_paths[1] + '.ro') 
        ##src_test_paths.append(dest_test_paths[0].replace(recv_dir, send_dir) + '.ro')
        ##src_test_paths.append(dest_test_paths[1].replace(recv_dir, send_dir) + '.ro')
        #try:
        #    [os.mkdir(t) for t in src_test_paths]
        #    [os.mkdir(t) for t in dest_test_paths]
        ## ==========================
        
        td_list = list(self.get_td(opts))
        cloning = len(td_list) < 2
        ts_list = list(self.get_ts(os.path.join(recv_dir, name) + '.*'))
        #ts_recv_list = list(self.get_ts(os.path.join(recv_dir, name) + '.*.ro'))
        ts_send_list = list(self.get_ts(os.path.join(send_dir, name) + '.*'))
         
        for idx,td in enumerate(td_list):

            if cloning:
                td_list
                ts_clones = set(ts_list) & set(ts_send_list)
                ts_to_clone set(ts_send_list) - set(ts_list)
                ts_to_delete set(ts_list) - set(ts_send_list)
                
            
            # how many snapshots are within the current timedelta window?
            ts_within_td = []
            if not cloning:

                # the newest timedelta window starts at now - 0
                if idx == 0:
                    td_prev = datetime.timedelta()
                
                self.ui.debug('Checking delta: ' + str(td))
                for ts in ts_list:
                    if (ts_now - td < ts) and (ts_now - td_prev > ts):
                        ts_within_td.append(ts)
         
                td_prev = td

            if idx == 0 and not ts_within_td:

                if cloning:
                    # intersection -> 
                    ts_clones = set(ts_recv_list) & set(ts_send_list)
                    ts_to_clone set(ts_send_list) - set(ts_recv_list)
                    ts_to_delete set(ts_recv_list) - set(ts_send_list)
                    
                    # FIXME
                    # - remove to_delete
                    # - 
                    


                
                self.ui.info('Taking snapshot {0}...'.format(recv_path))
         
                # are src & dest residing on the same root/subvol?
                same_root = send_path == recv_path

                # check reference snapshot consistency
                if not same_root:
                    # any reference snapshots for incremental backup?
                    for d in (send_dir, recv_dir):
                        refs_abspath = map(lambda x: os.path.join(d, x), sorted(os.listdir(d)))
                        refs_ro_suffix = filter(lambda x: re.search(name + '.*\.ro$', x), refs_abspath)
                        refs = []
                        for ref in refs_ro_suffix:
                            try:
                                ts = self.get_ts_of_path(ref.strip('.ro'))
                            except ValueError:
                                pass
                            else:
                                refs.append(ref)
                        if len(refs) > 1:
                            raise self.exc_class('more than one reference snapshot ({0}*.ro) found in {1}'.format(name, d))
                            
                    # check an existing reference snapshot also exists on sending side
                    parent_str = ''
                    if refs:
                        if not os.path.exists(refs[0].replace(recv_dir, send_dir)):
                            raise self.exc_class('no corresponding reference snapshot of {0} found on sending side'.format(name))
                        parent_str = '-p ' + refs[0]
                     
                # take snapshot of src with current ts
                self.dispatch('btrfs subvolume snapshot {2} {0} {1}{3}'.format(src_path,
                                                                               send_path,
                                                                               '' if same_root else '-r',
                                                                               '' if same_root else '.ro'))
                
                if not same_root:
                    self.dispatch('btrfs send {0} {1}.ro | btrfs receive {2}'.format(parent_str,
                                                                                     send_path,
                                                                                     recv_dir),
                                  output='stderr')
                     
                    # delete now obsolete reference snapshots
                    if refs:
                        self.dispatch('btrfs subvolume delete {0} {1}'.format(refs[0],
                                                                              refs[0].replace(recv_dir, send_dir)))
                        
                    # create rw snapshots
                    self.dispatch('btrfs subvolume snapshot {0}.ro {0}'.format(send_path))
                    self.dispatch('btrfs subvolume snapshot {0}.ro {0}'.format(recv_path))


            # FIXME
            # cloning!!!
            if len(td_list) == 1:
                # take all globed source dirs and clone them one by one to dest
                self.dispatch('btrfs send {0} {1}.ro | btrfs receive {2}'.format('bla',
                                                                                 send_path,
                                                                                 recv_dir),
                              output='stderr')
                    
            # keep only the newest snapshot in the oldest timedelta window
            if idx == len(td_list) - 1:
                ts_within_td = reversed(ts_within_td)
         
            for idx,ts in enumerate(ts_within_td):
                path = self.get_path_of_ts(recv_dir, name, ts)
                if idx > 0:
                    self.ui.info('Deleting snapshot: ' + path)
                    self.dispatch('btrfs subvolume delete ' + path)
                else:
                    self.ui.debug('Keeping snapshot: ' + path)

        #finally:
        #    [os.rmdir(t) for t in src_test_paths]
        #    [os.rmdir(t) for t in dest_test_paths]
            
    def info(self, src_path, dest_path, opts=''):
        # FIXME
        # display the snapshot sizes (wait on stable implementation of quota feature)
        pass

    def modify(self, src_path, dest_path, opts=''):
        # FIXME
        # check if it's possible to simply delete large snapshots (eg, created prior to
        # deleting some files) => is the timestamp structure recovering automatically?
        pass

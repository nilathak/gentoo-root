import datetime
import glob
import hashlib
import math
import os
import pylon.base as base
import re
import threading

snapshot_pattern = '%Y-%m-%dT%H-%M-%S'
snapshot_regex = '[0-9]*-[0-9]*-[0-9]*T[0-9]*-[0-9]*-[0-9]*'

class backup_btrfs(base.base):
    """
implement btrfs subvolume snapshot backups based on interval string

NOTES
- if !newest timedelta slot contains 0 timestamps => nothing
- if  newest timedelta slot contains 0 timestamps => take snapshot
- if !oldest timedelta slot contains more than 1 timestamp => keep oldest, delete others
- if  oldest timedelta slot contains more than 1 timestamp => keep newest, delete others
- if no timedeltas are specified, all new snapshots from src are cloned to dest, obsolete ones in dest are deleted
- deleting snapshot will always happen together with creating snapshots, since older deltas are always >= newer deltas 
- the single snapshot after the oldest timedelta is kept for/replaced after oldest-1 timedelta
- Taking snapshots of a subvolume is not a recursive process. If a snapshot of a subvolume is created,
  every subvolume or snapshot that the subvolume already contains is mapped to an empty directory of the
  same name inside the snapshot (https://en.wikipedia.org/wiki/Btrfs#Subvolumes_and_snapshots)
- Remote backup with send/receive are faster than simple rsync
- source snapshot paths must always be specified as absolute paths from root, not path to mount point,
  or otherwise the automatic extraction of incremental snapshot directory fails
- determining the sizes of specific snapshots (via quota & qgroup) is usually meaningless. deleting snapshots from within the
  timedelta grid simply shifts shared data to the neighboring snapshots. reduction in size can only be reached by simply
  deleting the oldest snapshots (which can easily done manually without any adm_backup operation).
  it would only make sense for snapshots which show a large "exclusive size" (3rd column in qgroup output), which can be elevated
  for snapshots containing many large transient files (downloads, caches, ...), but it's generally better to decrease
  snapshot retention time in this case
"""
    
    @classmethod
    def unique_logspace(cls, data_points, interval_range):
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
        for d in glob.glob(path):
            try:
                yield self.get_ts_of_path(d)
            except Exception:
                self.ui.warning('Failed to extract ts: ' + d)

    def get_btrfs_uuid(self, path):
        # 'filesystem show' is not stable, sometimes it just bails out without any output
        while True:
            output = self.dispatch('btrfs filesystem show {0}'.format(path),
                                   passive=True,
                                   output=None).stdout
            try:
                return re.search('uuid: (.*)', output[0]).group(1)
            except Exception:
                pass
           
    def do(self, src_path, dest_path, opts=''):
        
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
        same_fs = self.get_btrfs_uuid(send_dir) == self.get_btrfs_uuid(recv_dir)

        ts_now = self.get_ts_now()
        td_list = list(self.get_td(opts))
        
        # distinguish snapshot sets by hash (so timedeltas are not applied to send/receive references of other dests)
        hash_dir = recv_dir
        hash = hashlib.md5(hash_dir.encode('utf-8')).hexdigest()
        hash_glob = '*' + hash + '*'
        
        send_path = self.get_path_of_ts(send_dir, name + '.' + hash, ts_now)
        recv_path = self.get_path_of_ts(recv_dir, name + '.' + hash, ts_now)
        ts_send_list = sorted(list(self.get_ts(os.path.join(send_dir, name) + hash_glob)))
        ts_recv_list = sorted(list(self.get_ts(os.path.join(recv_dir, name) + hash_glob)))
        
        # - send/receive references must be read-only
        # - writeable clones on receiving side are left behind by interrupted send/receive operation
        ts_of_clones = sorted(list(set(ts_send_list) & set(ts_recv_list)))
        for ts in ts_of_clones:
            for d in (send_dir, recv_dir):
                path = self.get_path_of_ts(d, name + '.' + hash, ts)
                for l in self.dispatch('btrfs subvolume show ' + path,
                                       passive=True,
                                       output=None).stdout:
                    if 'Flags' in l:
                        if 'readonly' not in l:
                            self.ui.warning('Deleting writable clone: ' + path)
                            self.dispatch('btrfs subvolume delete -c ' + path,
                                          output='stderr')
                            if d is send_dir:
                                ts_send_list.remove(ts)
                            else:
                                ts_recv_list.remove(ts)

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
            ts_within_td = list()

            # the newest timedelta window starts at now - 0
            if idx == 0:
                td_prev = datetime.timedelta()
                
            self.ui.debug('Checking delta: ' + str(td))
            for ts in ts_recv_list:
                if (ts_now - td < ts) and (ts_now - td_prev > ts):
                    ts_within_td.append(ts)

            # FIXME
            # if ts_within_td is empty => ts_now -= (ts_now - td_prev) - next_ts_older_than_(ts_now_minus_td)
            # restart ts_within_td search with shifted ts_now
                    
            if idx == 0:

                if not ts_within_td:
                    snap_path = recv_path if same_fs else send_path
                    self.ui.info('Taking snapshot {0}...'.format(snap_path))
                    self.dispatch('btrfs subvolume snapshot -r {0} {1}'.format(src_path,
                                                                               snap_path),
                                  output='stderr')
                    ts_send_list.append(ts_now)
                    ts_within_td.append(ts_now)

                if not same_fs:

                    ts_of_clones = sorted(list(set(ts_send_list) & set(ts_recv_list)))
                    ts_to_clone  = sorted(list(set(ts_send_list) - set(ts_recv_list)))
                    ts_to_delete = sorted(list(set(ts_recv_list) - set(ts_send_list)))

                    # clone all new timestamps
                    #   btrfs sub send <snap 0>
                    #   for n=1 to N
                    #      btrfs sub send -p <snap n-1> <snap n>
                    #    
                    #   Or, in any order,
                    #    
                    #   btrfs sub send <snap s1>
                    #   for n=1 to N
                    #      btrfs sub send -c <snap s1> -c <snap s2> -c <snap s3> ... <snap sn>
                    #    
                    #   where each subvolume that's been sent before gets added as a -c to the
                    #   next send command. This second approach means that all possible
                    #   reflinks between subvolumes can be captured, but it will send all of
                    #   the metadata across each time. The first approach may lose some manual
                    #   reflink efficiency, but is better at sending only the necessary
                    #   changed metadata.
                    for ts in ts_to_clone:

                        # assemble string of clones timestamp paths
                        clone_str = ''
                        for clone in ts_of_clones:
                            clone_str = clone_str + ' -c ' + self.get_path_of_ts(send_dir, name + '.' + hash, clone)

                        # transfer reference snapshot and any reflink relations
                        # FIXME stdout of send before pipe is not surpressed
                        self.ui.info('Cloning to {0}...'.format(recv_path))
                        self.dispatch('btrfs send {0} {1} | btrfs receive {2}'.format(clone_str,
                                                                                      self.get_path_of_ts(send_dir, name + '.' + hash, ts),
                                                                                      recv_dir),
                                      output='stderr')

                        # add freshly cloned snapshot as new clone
                        ts_of_clones.append(ts)
                        # needed for proper timedelta cleanup at end of td loop
                        ts_recv_list.append(ts)

                    # rescan timedelta (needed since cloning multiple snapshots leads to ignored snapshots in subsequent timedelta cleanup,
                    # they would be deleted in a run without snapshot taking, but this approach is cleaner)
                    for ts in ts_recv_list:
                        if ((ts_now - td < ts) and
                            (ts_now - td_prev > ts) and
                            ts not in ts_within_td):
                            ts_within_td.append(ts)
                    ts_within_td.sort()
                        
                    # deleting obsolete reference snapshots on src
                    for ts in ts_of_clones[:-1]:
                        path = self.get_path_of_ts(send_dir, name + '.' + hash, ts)
                        self.ui.info('Deleting obsolete reference: ' + path)
                        self.dispatch('btrfs subvolume delete -c ' + path,
                                      output='stderr')

            # keep only the newest snapshot in the oldest timedelta window
            if (idx == len(td_list) - 1 or
                
                # - we need to keep newest snapshot in newest timedelta for
                #   incremental send/receive case
                # - a reference snapshot on src is only available for the newest timedelta
                # - this condition surfaces when going from small to large timedelta resolution
                idx == 0 and not same_fs):
                ts_within_td = reversed(ts_within_td)

            # keep only a sinlge snapshot within any given timedelta
            for idx,ts in enumerate(ts_within_td):
                path = self.get_path_of_ts(recv_dir, name + '.' + hash, ts)
                if idx > 0:
                    self.ui.info('Deleting snapshot: ' + path)
                    self.dispatch('btrfs subvolume delete -c ' + path,
                                  output='stderr')
                else:
                    self.ui.debug('Keeping snapshot: ' + path)

            td_prev = td

            
        self.ui.info('Saved {0} to {1}'.format(src_path, dest_path))
        #finally:
        #    [os.rmdir(t) for t in src_test_paths]
        #    [os.rmdir(t) for t in dest_test_paths]
            
    def info(self, src_path, dest_path, opts=''):
        pass

    def modify(self, src_path, dest_path, opts=''):
        pass

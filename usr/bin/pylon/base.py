# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
import sys
import threading
import types
import pylon.job
import pylon.ui

class script_error(Exception):
    'provide our own exception class for easy identification'

    @property
    def msg(self):
        return self._msg
    @property
    def owner(self):
        return self._owner

    def __init__(self, msg='No error info available', owner=None):
        self._msg = msg
        self._owner = owner

    def __str__(self):
        return self.msg

class base(object):
    'common base for python scripts'

    @property
    def exc_class(self):
        return self._exc_class
    @property
    def job_class(self):
        return self._job_class
    @property
    def jobs(self):
        return self._jobs
    @property
    def ui(self):
        return self._ui
    @property
    def ui_class(self):
        return self._ui_class

    def __init__(self,
                 exc_class=script_error,
                 job_class=pylon.job.job,
                 ui_class=pylon.ui.ui,
                 owner=None):
        # save type of overriding classes
        self._exc_class = exc_class
        self._job_class = job_class
        self._ui_class = ui_class
        if owner:
            self._exc_class = owner.exc_class
            self._job_class = owner.job_class

            # always reuse the interface of a calling script
            self._ui = owner.ui
        else:
            # delay ui creation until now, so 'self' is defined for
            # 'owner' option of ui
            self._ui = ui_class(self)
        self._jobs = {}

    def dispatch(self, cmd, output='both', passive=False, blocking=True):
        'dispatch a job (see job class for details)'

        job = self.job_class(ui=self.ui,
                             cmd=cmd,
                             output=output,
                             owner=self,
                             passive=passive,
                             blocking=blocking)
        if not blocking:
            # always keep a valid thread dependency tree
            parent = threading.current_thread()
            if parent not in self.jobs:
                self.jobs[parent] = []
            self.jobs[parent].append(job)

        return job()

    def join(self):
        'join all known child threads, perform cleanup of job lists'
        if len(self.jobs) > 0:
            parent = threading.current_thread()

            to_join = self.jobs[parent]
            while any(map(lambda x: x.thread.is_alive(), to_join)):
                for j in to_join:
                    j.join()

                # find zombie parents (add children to current thread)
                [self.jobs[parent].extend(v) for (k, v) in self.jobs.items() if not k.is_alive()]
                to_join = self.jobs[parent]

            unhandled_exc = any(map(lambda x: x.exc_info != None, to_join))

            # all children finished
            del self.jobs[parent]

            if unhandled_exc:
                raise self.exc_class('unhandled exception in child thread(s)')

    def run(self):
        'common entry point for debugging and exception purposes'

        # install our custom exception handler
        sys.excepthook = self.ui.excepthook

        self.ui.setup()
        self.run_core()
        self.ui.cleanup()

class memoize(object):
    def __init__(self, fn):
        self.cache = {}
        self.fn = fn
    def __get__(self, instance, cls=None):
        self.instance = instance
        return self
    def __call__(self,*args):
        if args in self.cache:
            return self.cache[args]
        if hasattr(self, 'instance'):
            object = self.cache[args] = self.fn(self.instance, *args)
        else:
            object = self.cache[args] = self.fn(*args)
        return object

def flatten(l):
    for el in l:
        if hasattr(el, '__iter__') and not isinstance(el, str):
            for sub in flatten(el):
                yield sub
        else:
            yield el

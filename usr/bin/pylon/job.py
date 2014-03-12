# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
import os
import pylon
import subprocess
import sys
import threading

class job(object):
    '''common base for (threaded) python jobs

blocking:
    set False to dispatch a thread.

passive:
    execute command even though dry_mode is enabled (eg. for passive data extraction).

output:
    this option is only valid when dispatching a command via the subprocess module.
    setting   | stdout            | stderr            | comments
    --------------------------------------------------------------------
    None      | pipe->var         | pipe->var         | var are the self.stdout and self.stderr variables
    "stdout"  | pipe->var->stdout | null              |
    "stderr"  | null              | pipe->var->stderr |
    "both"    | pipe->var->stdout | pipe->var->stderr | default
    "nopipes" | stdout            | stderr            | direct output without pipes (output cannot be used for postprocessing'''

    @property
    def exc_info(self):
        return self._exc_info
    @property
    def ret_val(self):
        return self._ret_val
    @property
    def stderr(self):
        return self._stderr
    @property
    def stdout(self):
        return self._stdout
    @property
    def thread(self):
        return self._thread
    @property
    def ui(self):
        return self._ui

    def __init__(self, ui, cmd, output='both', owner=None, passive=False, blocking=True):
        self._blocking = blocking
        self._cmd = cmd
        self._exc_info = None
        self._output = output
        self._owner = owner
        self._passive = passive
        self._threadname = threading.current_thread().name
        self._ret_val = None
        self._thread = threading.current_thread()
        if not blocking:
            self._thread = threading.Thread(target=self.exception_wrapper)
        self._ui = ui

    def __call__(self):

        # stay in current thread for blocking jobs, otherwise fork
        if self._blocking:
            self.exception_wrapper()
        else:
            self.thread.start()
        return self

    def join(self):
        'join back to caller thread'
        self.thread.join()

    def exception_wrapper(self):
        try:

            # assign meaningful output prefixes (use parent thread id for
            # blocking jobs)
            if threading.active_count() == 1:
                self._prefix = ''
            else:
                self._prefix = self.thread.name + ': '
                for h in self.ui.logger.handlers:
                    h.setFormatter(self.ui.formatter['threaded'])

            # python function?
            if hasattr(self._cmd, '__call__'):
                self._ret_val = self._cmd()

            # no? then assume it's a string containing an external command invocation
            else:
                self.exec_cmd()
        except Exception as e:
            # - save exception context to inform caller thread
            # - print exception here to include correct thread info
            if not self._blocking:
                self._exc_info = sys.exc_info()
                self.ui.excepthook(*self.exc_info)
            else:
                raise e
        finally:
            # reset the logger format if only Mainthread will be left
            if threading.active_count() <= 2:
                for h in self.ui.logger.handlers:
                    h.setFormatter(self.ui.formatter['default'])

    def exec_cmd(self):
        'do subprocess invocations on all supported architectures'
        self.ui.debug(self._cmd)
        self._stdout = []
        self._stderr = []
        if not self.ui.args.dry_run or self._passive:

            devnull = open(os.devnull, 'w')
            try:

                # quiet switch takes precedence
                if self.ui.args.quiet > 1:
                    self._output = None
                elif self.ui.args.quiet > 0:
                    # do not interfere with already configured None
                    if self._output:
                        self._output = 'stderr'

                # decode output string
                stdin  = None
                stdout = subprocess.PIPE
                stderr = subprocess.PIPE
                if self._output == 'nopipes':
                    stdout = None
                    stderr = None
                elif self._output == 'stdout':
                    stderr = devnull
                elif self._output == 'stderr':
                    stdout = devnull

                # windows subprocess
                if subprocess.mswindows:
                    self.ui.error('Windows platform not supported!')

                # POSIX subprocess
                else:
                    self._proc = subprocess.Popen(self._cmd, shell=True,
                                                  # always use bash
                                                  executable='/bin/bash',
                                                  stdin=stdin,
                                                  stdout=stdout,
                                                  stderr=stderr)

                    while self._proc.poll() == None:
                        (proc_stdout_b, proc_stderr_b) = self._proc.communicate(stdin)

                        if proc_stdout_b:
                            proc_stdout = proc_stdout_b.decode().rstrip(os.linesep).rsplit(os.linesep)
                            self.stdout.extend(proc_stdout)
                            if (self._output and
                                (self._output == 'both' or
                                 self._output == 'stdout')):
                                [sys.stdout.write(self._prefix + l + os.linesep) for l in proc_stdout]

                        if proc_stderr_b:
                            proc_stderr = proc_stderr_b.decode().rstrip(os.linesep).rsplit(os.linesep)
                            self.stderr.extend(proc_stderr)
                            if (self._output and
                                (self._output == 'both' or
                                 self._output == 'stderr')):
                                [sys.stderr.write(self._prefix + l + os.linesep) for l in proc_stderr]

                # can be caught anyway if a subprocess does not abide
                # to standard error codes
                if self._proc.returncode != 0:
                    raise self._owner.exc_class('error executing "%s"' % self._cmd , self)

            finally:
                devnull.close()
                self._ret_val = self._proc.returncode

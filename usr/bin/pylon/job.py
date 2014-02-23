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
        'join back to caller thread and propagate exception'
        self.thread.join()
        if self.exc_info and self._blocking:
            (type, val, tb) = self.exc_info
            raise type, val, tb

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
            self._exc_info = sys.exc_info()

            # print exception here to include correct thread info
            if self._blocking:
                pylon.base.reraise()
            else:
                self.ui.excepthook(*self.exc_info)
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
        if not self.ui.opts.dry_run or self._passive:

            devnull = open(os.devnull, 'w')
            try:

                # quiet switch takes precedence
                if self.ui.opts.quiet > 1:
                    self._output = None
                elif self.ui.opts.quiet > 0:
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
                    self.exec_cmd_win(stdin, stdout, stderr)

                # assume POSIX subprocess
                else:
                    self.exec_cmd_posix(stdin, stdout, stderr)

                # can be caught anyway if a subprocess does not abide
                # to standard error codes
                if self._proc.returncode != 0:
                    raise self._owner.exc_class('error executing "%s"' % self._cmd , self)

            finally:
                devnull.close()
                self._ret_val = self._proc.returncode

    def exec_cmd_posix(self, stdin, stdout, stderr):
        'partly a reimplementation of Popen.communicate to allow a non blocking communication when using pipes.'
        # fork it...
        self._proc = subprocess.Popen(self._cmd, shell=True,
                                     # always use bash
                                     executable='/bin/bash',
                                     stdin=stdin,
                                     stdout=stdout,
                                     stderr=stderr)

        import select
        write_set = []
        read_set = []
        if (not self._output or
            self._output == 'both' or
            self._output == 'stdout'):
            read_set.append(self._proc.stdout)
        if (not self._output or
            self._output == 'both' or
            self._output == 'stderr'):
            read_set.append(self._proc.stderr)

        while (read_set or write_set):
            rlist, wlist, xlist = select.select(read_set, write_set, [])

            proc_stdout = ''
            if self._proc.stdout in rlist:
                proc_stdout = self._proc.stdout.readline()
                if proc_stdout == '':
                    read_set.remove(self._proc.stdout)
            proc_stderr = ''
            if self._proc.stderr in rlist:
                proc_stderr = self._proc.stderr.readline()
                if proc_stderr == '':
                    read_set.remove(self._proc.stderr)

            # try to record everything regardless of self._output config
            if proc_stderr != '':
                # - saved as list of lines anyway, so strip newlines
                # - handle DOS newlines gracefully
                self.stderr.append(proc_stderr.rstrip(os.linesep))
                if self._output:
                    sys.stderr.write(self._prefix + proc_stderr)
            if proc_stdout != '':
                self.stdout.append(proc_stdout.rstrip(os.linesep))
                if self._output:
                    sys.stdout.write(self._prefix + proc_stdout)

        self._proc.wait()

    def exec_cmd_win(self, stdin, stdout, stderr):
        'partly a reimplementation of Popen.communicate to allow a non blocking communication when using pipes.'

        # needed for decisions below
        self._stdout = self._stderr = None

        # fork it...
        self._proc = subprocess.Popen(self._cmd, shell=True,
                                     stdin=stdin,
                                     stdout=stdout,
                                     stderr=stderr)

        def _process_stdout():
            proc_stdout = ''
            while self._proc.returncode == None:
                proc_stdout = self._proc.stdout.readline()
                if proc_stdout != '':
                    self.stdout.append(proc_stdout.rstrip(os.linesep))
                    if self._output:
                        sys.stdout.write(self._prefix + proc_stdout)

        def _process_stderr():
            proc_stderr = ''
            while self._proc.returncode == None:
                proc_stderr = self._proc.stderr.readline()
                if proc_stderr != '':
                    self.stderr.append(proc_stderr.rstrip(os.linesep))
                    if self._output:
                        sys.stderr.write(self._prefix + proc_stderr)

        if (not self._output or
            self._output == 'both' or
            self._output == 'stdout'):
            self._stdout = []
            stdout_thread = threading.Thread(target=_process_stdout)
            stdout_thread.start()
        if (not self._output or
            self._output == 'both' or
            self._output == 'stderr'):
            self._stderr = []
            stderr_thread = threading.Thread(target=_process_stderr)
            stderr_thread.start()

        self._proc.wait()

        if self.stdout:
            stdout_thread.join()
        else:
            self._stdout = []
        if self.stderr:
            stderr_thread.join()
        else:
            self._stderr = []

# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
import os
import pylon.job

class job(pylon.job.job):
    'modified output option handling, to allow mail reporting'

    import threading
    report_stream_lock = threading.Semaphore()

    def exec_cmd(self):
        try:
            super().exec_cmd()
        finally:
            if self.ui.args.mail and not self.ui.args.dry_run:
                with job.report_stream_lock:
                    if ((self._output == 'both' or
                        self._output == 'stdout') and
                        len(self._stdout) > 0):
                        self.ui.report_stream.write(self._prefix + (os.linesep + self._prefix).join(self._stdout) + os.linesep)
                    elif ((self._output == 'stderr') and
                          len(self._stderr) > 0):
                        self.ui.report_stream.write(self._prefix + (os.linesep + self._prefix).join(self._stderr) + os.linesep)

# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
import os
import pylon.ui

class ui(pylon.ui.ui):

    @property
    def fqdn(self):
        return self._fqdn
    @property
    def hostname(self):
        return self._hostname
    @property
    def report_stream(self):
        return self._report_stream

    def __init__(self, owner):
        super(ui, self).__init__(owner)

        # add handler for mail logging
        # FIXME replace StringIO module with io module
        import StringIO
        import logging
        self._report_stream = StringIO.StringIO()
        self._handler['mail'] = logging.StreamHandler(self._report_stream)
        self._handler['mail'].setFormatter(self.formatter['default'])
        self.logger.addHandler(self._handler['mail'])

        # hooray, more emails (alias needs to be set)...
        self._message_server = 'root@localhost'

    def configure(self):
        super(ui, self).configure()
        self.parser.add_option('--hostname', action='store',
                               help='force specific hostname')
        self.parser.add_option('-r', '--report', action='store_true',
                               help='generate additional mail report (def: root@localhost)')

    def validate(self):
        import socket
        self._hostname = socket.gethostname()
        # force hostname
        if self.opts.hostname:
            self._hostname = self.opts.hostname
        self._fqdn = socket.getfqdn(self._hostname)

    def cleanup(self, subject='report'):
        'send optional email with all output to global message server'
        if (self.opts.report and
            not self.opts.dry_run and
            len(self.report_stream.getvalue()) > 0):
            import email.MIMEText
            import smtplib
            m = email.MIMEText.MIMEText(self.report_stream.getvalue())
            m['From'] = self._owner.__class__.__name__ + '@' + self.fqdn
            m['To'] = self._message_server
            m['Subject'] = subject
            s = smtplib.SMTP(self._message_server.split('@')[1])
            s.set_debuglevel(0)
            s.sendmail(m['From'], m['To'], m.as_string())
            s.quit()

    def extract_doc_strings(self, find_pattern=None, replace_lambda=lambda x: ''):
        '''generate help strings for optparse from doc strings of prefixed functions.
           To be considered, a function name has to start with the respective class
           name followed by an underscore.'''
        if find_pattern == None:
            import re
            find_pattern = re.compile('^' + self._owner.__class__.__name__ + '_')
        def add_doc(x):
            doc = '#' + find_pattern.sub(replace_lambda(x),
                x.string)
            if getattr(self._owner, x.string).__doc__:
                doc += os.linesep + self._help_wrapper(getattr(self._owner, x.string).__doc__)
            return doc
        matches = [x for x in map(find_pattern.match, dir(self._owner)) if x != None]
        return os.linesep.join(map(add_doc, matches))

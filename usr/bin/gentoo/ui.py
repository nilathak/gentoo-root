# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
import os
import re
import socket
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
        super().__init__(owner)

        # add handler for mail logging
        import io
        import logging
        self._report_stream = io.StringIO()
        self._handler['mail'] = logging.StreamHandler(self._report_stream)
        self._handler['mail'].setFormatter(self.formatter['default'])
        self.logger.addHandler(self._handler['mail'])

        # hooray, more emails (alias needs to be set)...
        self._message_server = 'root@localhost'

        self.parser.add_argument('--mail', action='store_true',
                                 help='generate additional mail report (def: root@localhost)')

        # when using operations:
        # - use self.parser_common from here on instead of self.parser
        # - do not forget to run init_op_parser after all parser_common statements in __init__
        import argparse
        self.parser_common = argparse.ArgumentParser(conflict_handler='resolve',
                                                     parents=[self.parser])

    def init_op_parser(self):
        # define operation subparsers with common options if class methods
        # with specific prefix are present
        ops_pattern = re.compile('^{0}_(.*)'.format(self._owner.__class__.__name__))
        ops = [x for x in map(ops_pattern.match, dir(self._owner)) if x != None]
        if ops:
            subparsers = self.parser.add_subparsers(title='operations', dest='op')
            for op in ops:
                setattr(self, 'parser_' + op.group(1),
                        subparsers.add_parser(op.group(1),
                                              conflict_handler='resolve',
                                              parents=[self.parser_common],
                                              description=getattr(self._owner, op.string).__doc__,
                                              help=getattr(self._owner, op.string).__doc__))

    def setup(self):
        super().setup()

        self._hostname = socket.gethostname()
        self._fqdn = socket.getfqdn(self._hostname)

        self._report_subject = 'report'
        if hasattr(self.args, 'op'):
            self._report_subject = self.args.op

    def cleanup(self):
        'send optional email with all output to global message server'
        if (self.args.mail and
            not self.args.dry_run and
            len(self.report_stream.getvalue()) > 0):
            from email.mime.text import MIMEText
            import smtplib
            m = MIMEText(self.report_stream.getvalue())
            m['From'] = self._owner.__class__.__name__ + '@' + self.fqdn
            m['To'] = self._message_server
            m['Subject'] = self._report_subject
            s = smtplib.SMTP(self._message_server.split('@')[1])
            s.set_debuglevel(0)
            s.sendmail(m['From'], m['To'], m.as_string())
            s.quit()

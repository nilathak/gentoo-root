# ====================================================================
# Copyright (c) Hannes Schweizer <hschweizer@gmx.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3, or (at your option)
# any later version.
# ====================================================================
import logging
import sys

# hack to enable newlines in optparse help texts
import optparse
import os
import textwrap
class FixedTextWrapper(textwrap.TextWrapper):
    # fix textwrap behavior in Windows
    def fill(self, text):
        return os.linesep.join(self.wrap(text))
class TextWrapper:
    @staticmethod
    def wrap(text, width, **kw):
        result = []
        w = FixedTextWrapper(width=width, **kw)
        for line in text.split(os.linesep):
            result.extend(w.wrap(line))
        return result
    @staticmethod
    def fill(text, width, **kw):
        result = []
        w = FixedTextWrapper(width=width, **kw)
        for line in text.split(os.linesep):
            result.append(w.fill(line))
        return os.linesep.join(result)
optparse.textwrap = TextWrapper()

class ui(object):
    'nice command line user interface class used by pylon based scripts'
    EXT_INFO = logging.INFO - 1

    @property
    def args(self):
        return self._args
    @property
    def formatter(self):
        return self._formatter
    @property
    def logger(self):
        return self._logger
    @property
    def opts(self):
        return self._opts
    @property
    def owner(self):
        return self._owner
    @property
    def parser(self):
        return self._parser

    def __init__(self, owner):
        self._owner = owner

        # define additional logging level for a better verbosity granularity
        logging.addLevelName(ui.EXT_INFO, 'INFO')

        # set logger name to class name
        self._logger = logging.getLogger(self.owner.__class__.__name__)

        # define format of logger output
        fmt_str = '### %(name)s(%(asctime)s) %(levelname)s: %(message)s'
        self._formatter = {}
        self.formatter['default']  = logging.Formatter(fmt_str)
        self.formatter['threaded'] = logging.Formatter('%(threadName)s: ' + fmt_str)

        # add default handler for logging on stdout
        self._handler = {}
        self._handler['stdout'] = logging.StreamHandler(sys.stdout)
        self._handler['stdout'].setFormatter(self.formatter['default'])
        self.logger.addHandler(self._handler['stdout'])

        self.configure()

    def configure(self):
        'process configuration before setting up UI'
        import optparse

        # disable shortcut of previously added option
        self._parser = optparse.OptionParser(conflict_handler='resolve')

        # take an existing class doc string from our owner and set it as usage message
        if self.owner.__doc__:
            self.parser.usage = self.owner.__doc__

        # define the common basic set of options
        self.parser.add_option('--dry_run', action='store_true',
                               help='switch to passive behavior (no subprocess execution)')
        self.parser.add_option('-q', action='count', dest='quiet', default=0,
                               help='quiet output (multiply for more silence)')
        self.parser.add_option('--traceback', action='store_true',
                               help='enable python traceback for debugging purposes')
        self.parser.add_option('-v', action='count', dest='verbosity', default=0,
                               help='verbose output (multiply for more verbosity)')

    def validate(self):
        'stub for command line argument validation'
        pass

    def setup(self):
        (self._opts, self._args) = self.parser.parse_args()
        try:
            self.validate()
        except self.owner.exc_class:
            self.parser.print_help()
            raise

        # determine default verbosity behavior
        l = logging.INFO
        if self.opts.verbosity > 1 or self.opts.dry_run or self.opts.traceback:
            l = logging.DEBUG
        elif self.opts.verbosity > 0:
            l = ui.EXT_INFO

        # quiet switch takes precedence
        if self.opts.quiet > 1:
            l = logging.ERROR
        elif self.opts.quiet > 0:
            l = logging.WARNING
        self.logger.setLevel(l)

    def cleanup(self):
        'stub for basic cleanup stuff'
        pass

    def handle_exception_gracefully(self, e_type):
        'returns True if an exception should NOT be thrown at python interpreter'
        return (
            # no traceback for invalid options case
            (hasattr(self, 'opts') and not self.opts.traceback) or

            # catch only objects deriving from Exception. Omit trivial
            # things like KeyboardInterrupt (derives from BaseException)
            not issubclass(e_type, Exception)
            )

    def excepthook(self, e_type, e_val, e_tb):
        'pipe exceptions to logger, control traceback display. default exception handler will be replaced by this function'

        # switch to a more passive exception handling mechanism if
        # other threads are still active
        origin = 'default'
        if len(self.owner.jobs) > 0:
            origin = 'thread'

        if self.handle_exception_gracefully(e_type):
            self.error(repr(e_type) + ' ' + str(e_val))
            if origin == 'default':
                self.cleanup()

                # generate error != 0
                sys.exit(1)

        else:
            if origin == 'thread':
                self.logger.exception('Traceback')
            else:
                # avoid losing any traceback info
                sys.__excepthook__(e_type, e_val, e_tb)

    def _help_wrapper(self, text, width=None):
        'modified textwrapper function which will work inside of optparse strings'
        if width is None:
            width = self.parser.formatter.width - self.parser.formatter.help_position
        wrapper = FixedTextWrapper(initial_indent    ='  ',
                                   subsequent_indent ='  ',
                                   width=width)
        return os.linesep.join([wrapper.fill(l) for l in text.split(os.linesep)])

    # logging level wrapper functions
    def debug(self, msg):
        self.logger.debug(msg)
    def error(self, msg):
        self.logger.error(msg)
    def ext_info(self, msg):
        self.logger.log(ui.EXT_INFO, msg)
    def info(self, msg):
        self.logger.info(msg)
    def warning(self, msg):
        self.logger.warning(msg)

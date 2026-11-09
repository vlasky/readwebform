"""
Command-line interface for readwebform.
"""

import argparse
import math
import sys

from . import __version__
from .multipart import parse_size_limit
from .output import DEFAULT_ENV_PREFIX, is_valid_var_name

EXIT_INVALID_ARGUMENT = 8


class _ArgumentParser(argparse.ArgumentParser):
    """ArgumentParser whose usage errors exit with the documented code 8."""

    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(EXIT_INVALID_ARGUMENT, f'{self.prog}: error: {message}\n')


class ReadWebFormArgumentParser:
    """Custom argument parser for readwebform."""

    def __init__(self):
        self.parser = _ArgumentParser(
            prog='readwebform',
            description='Gather structured user input through a temporary web form',
            epilog='See https://github.com/vlasky/readwebform for documentation and examples.'
        )
        self._setup_arguments()

    def _setup_arguments(self):
        """Configure all command-line arguments."""
        self.parser.add_argument('--version', action='version', version=f'%(prog)s {__version__}')

        input_group = self.parser.add_argument_group('input sources (mutually exclusive)')
        input_group.add_argument(
            '--html',
            metavar='<string>',
            help='Inline HTML fragment or document containing one <form> element'
        )
        input_group.add_argument(
            '--htmlfile',
            metavar='<path>',
            help='Path to an HTML file containing a form'
        )
        input_group.add_argument(
            '--field',
            action='append',
            metavar='<spec>',
            help='Declaratively define a form field (format: name:type[:label][:options]). '
                 'May be given multiple times.'
        )

        presentation_group = self.parser.add_argument_group('presentation options')
        presentation_group.add_argument(
            '--title',
            metavar='<string>',
            help='Page title shown above the form and in the browser tab'
        )
        presentation_group.add_argument(
            '--text',
            metavar='<string>',
            help='Instructional text shown above the form'
        )

        server_group = self.parser.add_argument_group('server configuration')
        server_group.add_argument(
            '--host',
            metavar='<ip>',
            default='127.0.0.1',
            help='IPv4 or IPv6 address to bind to (default: 127.0.0.1)'
        )
        server_group.add_argument(
            '--port',
            type=int,
            metavar='<int>',
            help='TCP port (default: auto-select a free port)'
        )
        server_group.add_argument(
            '--cert',
            metavar='<path>',
            help='Path to SSL certificate file (PEM format) for HTTPS'
        )
        server_group.add_argument(
            '--key',
            metavar='<path>',
            help='Path to SSL private key file (PEM format) for HTTPS'
        )

        upload_group = self.parser.add_argument_group('upload options')
        upload_group.add_argument(
            '--max-file-size',
            metavar='<limit>',
            help='Maximum individual upload size (e.g. 5M, 200K)'
        )
        upload_group.add_argument(
            '--max-total-size',
            metavar='<limit>',
            help='Maximum total request size (e.g. 20M, 1G; default: 20M)'
        )
        upload_group.add_argument(
            '--upload-dir',
            metavar='<path>',
            help='Directory to save uploaded files in (default: a new temporary directory)'
        )

        timeout_group = self.parser.add_argument_group('timeout configuration')
        timeout_group.add_argument(
            '--timeout',
            type=float,
            metavar='<seconds>',
            default=300,
            help='Max time to wait for submission in seconds (default: 300)'
        )
        timeout_group.add_argument(
            '--reset-timeout-on-error',
            type=self._str_to_bool,
            metavar='<bool>',
            default=True,
            help='Restart the timeout after a recoverable error (default: true)'
        )

        output_group = self.parser.add_argument_group('output format')
        output_group.add_argument(
            '--json',
            action='store_true',
            default=True,
            help='Output result as JSON on stdout (default; accepted for compatibility)'
        )
        output_group.add_argument(
            '--envfile',
            metavar='<path>',
            help='Write sanitized export statements to this file (mode 0600)'
        )
        output_group.add_argument(
            '--print-env',
            action='store_true',
            help='Print sanitized export statements to stdout instead of JSON'
        )
        output_group.add_argument(
            '--env-prefix',
            metavar='<prefix>',
            default=DEFAULT_ENV_PREFIX,
            help=f'Prefix for environment variable names (default: {DEFAULT_ENV_PREFIX})'
        )
        output_group.add_argument(
            '--url-file',
            metavar='<path>',
            help='Write the form URL to this file once the server is listening'
        )

        browser_group = self.parser.add_argument_group('browser options')
        browser_group.add_argument(
            '--launch-browser',
            nargs='?',
            const='',
            metavar='<path>',
            help='Open the form in a browser even when no desktop is detected, optionally '
                 'with a specific browser executable. By default the browser opens '
                 'automatically when a desktop is available (not in CI or over SSH).'
        )
        browser_group.add_argument(
            '--no-launch-browser',
            action='store_true',
            help='Never open a browser; just print the URL'
        )

        form_group = self.parser.add_argument_group('form generation')
        form_group.add_argument(
            '--no-submit-button',
            action='store_true',
            help='Omit the automatic submit button in declarative mode'
        )
        form_group.add_argument(
            '--submit-label',
            metavar='<text>',
            default='Submit',
            help='Label for the submit button (default: Submit)'
        )
        form_group.add_argument(
            '--no-cancel-button',
            action='store_true',
            help='Omit the cancel button in declarative mode'
        )
        form_group.add_argument(
            '--cancel-label',
            metavar='<text>',
            default='Cancel',
            help='Label for the cancel button (default: Cancel)'
        )

    @staticmethod
    def _str_to_bool(value: str) -> bool:
        """Convert a string to a boolean for --reset-timeout-on-error."""
        if isinstance(value, bool):
            return value
        if value.lower() in ('yes', 'true', 't', 'y', '1'):
            return True
        if value.lower() in ('no', 'false', 'f', 'n', '0'):
            return False
        raise argparse.ArgumentTypeError(f'Boolean value expected, got: {value}')

    def parse_args(self, args=None):
        """Parse command-line arguments and validate them."""
        parsed = self.parser.parse_args(args)
        self._validate_args(parsed)
        return parsed

    def _validate_args(self, args):
        """Validate argument combinations."""
        input_sources = sum([
            args.html is not None,
            args.htmlfile is not None,
            args.field is not None
        ])
        if input_sources > 1:
            self.parser.error('Only one input source allowed: --html, --htmlfile, or --field')

        if not math.isfinite(args.timeout) or args.timeout <= 0:
            self.parser.error('--timeout must be a positive, finite number of seconds')

        if args.port is not None and (args.port < 1 or args.port > 65535):
            self.parser.error('--port must be between 1 and 65535')

        if args.launch_browser is not None and args.no_launch_browser:
            self.parser.error('--launch-browser and --no-launch-browser are mutually exclusive')

        if args.cert and not args.key:
            self.parser.error('--cert requires --key')
        if args.key and not args.cert:
            self.parser.error('--key requires --cert')

        for option in ('max_file_size', 'max_total_size'):
            value = getattr(args, option)
            if value is not None:
                try:
                    parse_size_limit(value)
                except ValueError:
                    flag = '--' + option.replace('_', '-')
                    self.parser.error(f'{flag}: invalid size {value!r} (use e.g. 5M, 200K, 1G, or bytes)')

        if not is_valid_var_name(args.env_prefix + 'X'):
            self.parser.error('--env-prefix must contain only letters, digits and underscores '
                              'and not start with a digit')


def main():
    """Main entry point for the readwebform CLI."""
    parser = ReadWebFormArgumentParser()
    args = parser.parse_args()

    from .core import run_readwebform
    return run_readwebform(args)


if __name__ == '__main__':
    sys.exit(main())

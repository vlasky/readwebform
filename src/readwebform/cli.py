"""
Command-line interface for readwebform.
"""

import argparse
import sys
from typing import Optional


class ReadWebFormArgumentParser:
    """Custom argument parser for readwebform."""

    def __init__(self):
        self.parser = argparse.ArgumentParser(
            prog='readwebform',
            description='Gather structured user input through a temporary web form',
            epilog='See https://github.com/vlasky/readwebform for documentation and examples.'
        )
        self._setup_arguments()

    def _setup_arguments(self):
        """Configure all command-line arguments."""

        # Input mode arguments (mutually exclusive)
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
            help='Declaratively define form field (format: name:type[:label][:options]). May be specified multiple times.'
        )

        # Presentation arguments
        presentation_group = self.parser.add_argument_group('presentation options')
        presentation_group.add_argument(
            '--title',
            metavar='<string>',
            help='Page title shown above the form'
        )
        presentation_group.add_argument(
            '--text',
            metavar='<string>',
            help='Instructional text shown above the form'
        )

        # Server configuration
        server_group = self.parser.add_argument_group('server configuration')
        server_group.add_argument(
            '--host',
            metavar='<ip>',
            default='127.0.0.1',
            help='Host/IP to bind to (default: 127.0.0.1)'
        )
        server_group.add_argument(
            '--port',
            type=int,
            metavar='<int>',
            help='TCP port (default: auto-select free port)'
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

        # Upload limits
        upload_group = self.parser.add_argument_group('upload limits')
        upload_group.add_argument(
            '--max-file-size',
            metavar='<limit>',
            help='Maximum individual upload size (e.g., 5M, 200K)'
        )
        upload_group.add_argument(
            '--max-total-size',
            metavar='<limit>',
            help='Maximum total upload size (e.g., 20M, 1G)'
        )

        # Timeout configuration
        timeout_group = self.parser.add_argument_group('timeout configuration')
        timeout_group.add_argument(
            '--timeout',
            type=int,
            metavar='<seconds>',
            default=300,
            help='Max time to wait for submission in seconds (default: 300)'
        )
        timeout_group.add_argument(
            '--reset-timeout-on-error',
            type=self._str_to_bool,
            metavar='<bool>',
            default=True,
            help='Reset timeout on recoverable errors (default: true)'
        )

        # Output format
        output_group = self.parser.add_argument_group('output format')
        output_group.add_argument(
            '--json',
            action='store_true',
            default=True,
            help='Output result as JSON (default)'
        )
        output_group.add_argument(
            '--envfile',
            metavar='<path>',
            help='Write sanitized export statements to file'
        )
        output_group.add_argument(
            '--print-env',
            action='store_true',
            help='Print sanitized environment variable exports to stdout'
        )

        # Browser launch
        browser_group = self.parser.add_argument_group('browser options')
        browser_group.add_argument(
            '--launch-browser',
            nargs='?',
            const='',
            metavar='<path>',
            help='Launch web browser (system default if no path provided)'
        )

        # Form generation
        form_group = self.parser.add_argument_group('form generation')
        form_group.add_argument(
            '--no-submit-button',
            action='store_true',
            help='Disable automatic submit button in declarative mode'
        )
        form_group.add_argument(
            '--no-cancel-button',
            action='store_true',
            help='Disable cancel button in declarative mode'
        )
        form_group.add_argument(
            '--cancel-label',
            metavar='<text>',
            default='Cancel',
            help='Label for cancel button (default: Cancel)'
        )

    @staticmethod
    def _str_to_bool(value: str) -> bool:
        """Convert string to boolean for --reset-timeout-on-error."""
        if isinstance(value, bool):
            return value
        if value.lower() in ('yes', 'true', 't', 'y', '1'):
            return True
        elif value.lower() in ('no', 'false', 'f', 'n', '0'):
            return False
        else:
            raise argparse.ArgumentTypeError(f'Boolean value expected, got: {value}')

    def parse_args(self, args=None):
        """Parse command-line arguments and validate."""
        parsed = self.parser.parse_args(args)
        self._validate_args(parsed)
        return parsed

    def _validate_args(self, args):
        """Validate argument combinations."""
        # Check for mutually exclusive input sources
        input_sources = sum([
            args.html is not None,
            args.htmlfile is not None,
            args.field is not None
        ])

        if input_sources > 1:
            self.parser.error(
                'Only one input source allowed: --html, --htmlfile, or --field'
            )

        # Validate timeout
        if args.timeout <= 0:
            self.parser.error('--timeout must be a positive integer')

        # Validate port if specified
        if args.port is not None and (args.port < 1 or args.port > 65535):
            self.parser.error('--port must be between 1 and 65535')

        # Validate cert/key pair - both must be provided together
        if args.cert and not args.key:
            self.parser.error('--cert requires --key')
        if args.key and not args.cert:
            self.parser.error('--key requires --cert')


def main():
    """Main entry point for readwebform CLI."""
    parser = ReadWebFormArgumentParser()
    args = parser.parse_args()

    # Import here to avoid circular dependencies
    from .core import run_readwebform

    try:
        return run_readwebform(args)
    except KeyboardInterrupt:
        print('\n\nInterrupted by user', file=sys.stderr)
        return 1
    except Exception as e:
        print(f'Error: {e}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())

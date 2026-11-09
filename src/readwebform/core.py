"""
Core orchestration for readwebform: load, validate, serve, output.
"""

import sys
import traceback
from typing import Optional

from .browser import gui_available
from .forms import FieldSpec, generate_form_html
from .multipart import parse_size_limit
from .output import format_env_output, format_json_output, write_env_file
from .parser import inject_csrf_token, validate_html, wrap_html_fragment
from .server import FormServer

# Exit codes
EXIT_SUCCESS = 0
EXIT_INTERNAL_ERROR = 1
EXIT_INVALID_HTML = 2
EXIT_READ_ERROR = 3
EXIT_BROWSER_LAUNCH_ERROR = 4   # reserved: browser failures only warn
EXIT_TIMEOUT = 5
EXIT_UPLOAD_SIZE_EXCEEDED = 6   # reserved: oversized uploads are recoverable (HTTP 413)
EXIT_CANCELLED = 7
EXIT_INVALID_ARGUMENT = 8
EXIT_INTERRUPTED = 130


class InputError(Exception):
    """A problem with the form input that maps to a specific exit code."""

    def __init__(self, message: str, exit_code: int):
        super().__init__(message)
        self.exit_code = exit_code


def run_readwebform(args) -> int:
    """
    Main execution function for readwebform.

    Args:
        args: Parsed command-line arguments

    Returns:
        Exit code
    """
    try:
        try:
            html = load_html(args)
        except InputError as e:
            print(f'Error: {e}', file=sys.stderr)
            return e.exit_code

        html = wrap_html_fragment(html, args.title, args.text)

        warn_no_submit = not (args.field and not args.no_submit_button)
        valid, error = validate_html(html, warn_no_submit=warn_no_submit)
        if not valid:
            print(f'Error: {error}', file=sys.stderr)
            return EXIT_INVALID_HTML

        try:
            max_file_size = parse_size_limit(args.max_file_size)
            max_total_size = parse_size_limit(args.max_total_size)
        except ValueError as e:
            print(f'Error: Invalid size limit: {e}', file=sys.stderr)
            return EXIT_INVALID_ARGUMENT

        server = FormServer(
            html='',  # set after CSRF injection
            host=args.host,
            port=args.port,
            max_file_size=max_file_size,
            max_total_size=max_total_size,
            timeout=args.timeout,
            reset_timeout_on_error=args.reset_timeout_on_error,
            cert_file=getattr(args, 'cert', None),
            key_file=getattr(args, 'key', None),
            upload_dir=getattr(args, 'upload_dir', None),
        )
        server.html = inject_csrf_token(html, server.csrf_token, server.endpoint)

        if args.host in ('0.0.0.0', '::'):
            print('Warning: Binding to all interfaces. The form will be reachable from other machines.',
                  file=sys.stderr)
            print('         Consider --host 127.0.0.1 for local-only access.', file=sys.stderr)

        browser_path = resolve_browser_launch(args)

        try:
            success, form_data, file_metadata, cancelled = server.serve(
                launch_browser_path=browser_path,
                url_file=getattr(args, 'url_file', None)
            )
        except KeyboardInterrupt:
            _emit_failure(args, 'interrupted')
            print('\nInterrupted by user', file=sys.stderr)
            return EXIT_INTERRUPTED

        if cancelled:
            _emit_failure(args, 'cancelled')
            print('Form submission cancelled by user', file=sys.stderr)
            return EXIT_CANCELLED

        if not success or form_data is None:
            _emit_failure(args, 'timeout')
            print('Error: Timeout waiting for submission', file=sys.stderr)
            return EXIT_TIMEOUT

        env_prefix = getattr(args, 'env_prefix', 'WEBFORM_')
        if args.envfile:
            write_env_file(args.envfile, form_data.fields, env_prefix, file_metadata)
        if args.print_env:
            print(format_env_output(form_data.fields, env_prefix, file_metadata))
        else:
            print(format_json_output(form_data.fields, file_metadata, success=True))

        return EXIT_SUCCESS

    except KeyboardInterrupt:
        _emit_failure(args, 'interrupted')
        print('\nInterrupted by user', file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as e:
        print(f'Internal error: {e}', file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return EXIT_INTERNAL_ERROR


LOOPBACK_HOSTS = ('127.0.0.1', '::1', 'localhost')


def is_loopback_host(host: str) -> bool:
    return host in LOOPBACK_HOSTS or host.startswith('127.')


def resolve_browser_launch(args) -> Optional[str]:
    """
    Decide whether, and with which browser, to open the form.

    Returns None (do not launch), '' (system default browser) or a browser
    executable path, following these rules in order:

    1. ``--no-launch-browser``: never.
    2. ``--launch-browser [path]``: always, with that browser.
    3. Otherwise launch the default browser only when a desktop is detected
       (see ``browser.gui_available``) AND the form is for this machine's
       user: the host is a loopback address (a non-loopback host means the
       URL is for someone else) and no ``--url-file`` was requested (which
       means the caller is handling the URL itself).
    """
    if getattr(args, 'no_launch_browser', False):
        return None
    if args.launch_browser is not None:
        return args.launch_browser
    if not is_loopback_host(args.host):
        return None
    if getattr(args, 'url_file', None):
        return None
    return '' if gui_available() else None


def _emit_failure(args, error: str) -> None:
    """Print the JSON failure envelope unless env output was requested."""
    if not args.print_env:
        print(format_json_output({}, {}, success=False, error=error))


def load_html(args) -> str:
    """
    Load HTML from the input source selected by the arguments.

    Priority: --html, --htmlfile, --field, then stdin.

    Raises:
        InputError: If the input cannot be read or is invalid
    """
    if args.html is not None:
        return args.html

    if args.htmlfile is not None:
        try:
            with open(args.htmlfile, encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError as e:
            raise InputError(f'File not found: {args.htmlfile}', EXIT_READ_ERROR) from e
        except PermissionError as e:
            raise InputError(f'Permission denied: {args.htmlfile}', EXIT_READ_ERROR) from e
        except UnicodeDecodeError as e:
            raise InputError(f'{args.htmlfile} is not valid UTF-8: {e}', EXIT_READ_ERROR) from e
        except OSError as e:
            raise InputError(f'Cannot read {args.htmlfile}: {e}', EXIT_READ_ERROR) from e

    if args.field:
        return generate_html_from_fields(
            args.field,
            args.no_submit_button,
            getattr(args, 'no_cancel_button', False),
            getattr(args, 'cancel_label', 'Cancel'),
            getattr(args, 'submit_label', 'Submit'),
        )

    return read_stdin()


def generate_html_from_fields(field_specs: list, no_submit_button: bool,
                              no_cancel_button: bool = False,
                              cancel_label: str = 'Cancel',
                              submit_label: str = 'Submit') -> str:
    """
    Generate form HTML from declarative field specification strings.

    Raises:
        InputError: If a field specification is invalid (exit code 2)
    """
    fields = []
    for spec in field_specs:
        try:
            fields.append(FieldSpec.parse(spec))
        except ValueError as e:
            raise InputError(str(e), EXIT_INVALID_HTML) from e

    return generate_form_html(fields, add_submit_button=not no_submit_button,
                              add_cancel_button=not no_cancel_button, cancel_label=cancel_label,
                              submit_label=submit_label)


def read_stdin() -> str:
    """Read HTML from stdin."""
    if sys.stdin.isatty():
        print('Reading HTML from stdin (press Ctrl+D when done)...', file=sys.stderr)
    return sys.stdin.read()

"""
Core orchestration for readwebform: load, validate, serve, output.
"""

import ipaddress
import os
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

        try:
            check_paths(args)
        except InputError as e:
            print(f'Error: {e}', file=sys.stderr)
            return e.exit_code

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
            url_host=getattr(args, 'url_host', None),
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
        except OSError as e:
            # The server could not start (port in use, bad host, bad
            # certificate...). That is the user's environment, not a bug, so
            # no traceback. The server has usually printed the reason.
            if not getattr(e, 'readwebform_reported', False):
                print(f'Error: {e}', file=sys.stderr)
            if getattr(e, 'readwebform_file_error', False):
                return EXIT_READ_ERROR
            return EXIT_INTERNAL_ERROR

        if cancelled:
            _emit_failure(args, 'cancelled')
            print('Form submission cancelled by user', file=sys.stderr)
            return EXIT_CANCELLED

        if not success or form_data is None:
            _emit_failure(args, 'timeout')
            print('Error: Timeout waiting for submission', file=sys.stderr)
            return EXIT_TIMEOUT

        env_prefix = getattr(args, 'env_prefix', 'WEBFORM_')
        envfile_error = None
        if args.envfile:
            try:
                write_env_file(args.envfile, form_data.fields, env_prefix, file_metadata)
            except OSError as e:
                envfile_error = e

        # Always print the result, even when the envfile could not be
        # written: the user has already filled in the form.
        if args.print_env:
            print(format_env_output(form_data.fields, env_prefix, file_metadata))
        else:
            print(format_json_output(form_data.fields, file_metadata, success=True))

        if envfile_error is not None:
            print(f'Error: Cannot write the envfile {args.envfile}: {envfile_error}', file=sys.stderr)
            print('       The submission was still printed to stdout.', file=sys.stderr)
            return EXIT_READ_ERROR

        return EXIT_SUCCESS

    except KeyboardInterrupt:
        _emit_failure(args, 'interrupted')
        print('\nInterrupted by user', file=sys.stderr)
        return EXIT_INTERRUPTED
    except Exception as e:
        if _caused_by_interrupt(e):
            _emit_failure(args, 'interrupted')
            print('\nInterrupted by user', file=sys.stderr)
            return EXIT_INTERRUPTED
        print(f'Internal error: {e}', file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return EXIT_INTERNAL_ERROR


def is_loopback_host(host: str) -> bool:
    """
    True for 'localhost' and any loopback IP literal (127.0.0.0/8, ::1 in any
    spelling). Other hostnames are treated as non-loopback without a DNS
    lookup, so a name like 127.example.invalid does not count.
    """
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host.strip('[]')).is_loopback
    except ValueError:
        return False


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


def _check_creatable_dir(path: str, what: str) -> None:
    """Raise InputError unless path is, or can be created as, a writable directory."""
    path = os.path.realpath(path)
    if os.path.exists(path):
        if not os.path.isdir(path):
            raise InputError(f'{what} {path} is not a directory', EXIT_READ_ERROR)
        if not os.access(path, os.W_OK | os.X_OK):
            raise InputError(f'{what} {path} is not writable', EXIT_READ_ERROR)
        return
    # It will be created on demand: the nearest existing ancestor decides
    ancestor = os.path.dirname(path)
    while ancestor and not os.path.exists(ancestor) and os.path.dirname(ancestor) != ancestor:
        ancestor = os.path.dirname(ancestor)
    if not os.path.isdir(ancestor):
        raise InputError(f'{what} {path} cannot be created: {ancestor} is not a directory',
                         EXIT_READ_ERROR)
    if not os.access(ancestor, os.W_OK | os.X_OK):
        raise InputError(f'{what} {path} cannot be created: {ancestor} is not writable',
                         EXIT_READ_ERROR)


def _check_writable_file(path: str, what: str, will_be_created: Optional[str] = None) -> None:
    """
    Raise InputError unless a file can be created or overwritten at path.

    will_be_created names a directory that readwebform may create later (the
    upload directory); a file inside it cannot be checked yet and is allowed.
    """
    if os.path.isdir(path):
        raise InputError(f'{what} {path} is a directory', EXIT_READ_ERROR)
    target = os.path.realpath(path)  # follows a symlink, including a dangling one
    if os.path.exists(target):
        if not os.access(target, os.W_OK):
            raise InputError(f'{what} {path} is not writable', EXIT_READ_ERROR)
        return
    parent = os.path.dirname(target) or '.'
    if not os.path.isdir(parent):
        if will_be_created and os.path.realpath(will_be_created) == parent:
            return
        raise InputError(f'{what} {path}: the directory {parent} does not exist', EXIT_READ_ERROR)
    if not os.access(parent, os.W_OK | os.X_OK):
        raise InputError(f'{what} {path}: the directory {parent} is not writable', EXIT_READ_ERROR)


def check_paths(args) -> None:
    """
    Check the file arguments before the form is shown.

    Finding out that the envfile cannot be written only after the user has
    filled in the form would waste their submission, so fail now instead.
    This is a courtesy check, not a guarantee: the write can still fail later.

    Raises:
        InputError: with exit code 3 if a path is unusable
    """
    upload_dir = getattr(args, 'upload_dir', None)
    if upload_dir:
        _check_creatable_dir(upload_dir, 'The upload directory')
    envfile = getattr(args, 'envfile', None)
    if envfile:
        _check_writable_file(envfile, 'The envfile', will_be_created=upload_dir)
    url_file = getattr(args, 'url_file', None)
    if url_file:
        _check_writable_file(url_file, 'The URL file')
    for option, what in (('cert', 'The certificate'), ('key', 'The key')):
        path = getattr(args, option, None)
        if not path:
            continue
        # Not isfile(): a named pipe or /dev/fd/N from process substitution
        # is a legitimate way to pass a key.
        if os.path.isdir(path):
            raise InputError(f'{what} file {path} is a directory', EXIT_READ_ERROR)
        if not os.path.exists(path):
            raise InputError(f'{what} file {path} does not exist', EXIT_READ_ERROR)
        if not os.access(path, os.R_OK):
            raise InputError(f'{what} file {path} is not readable', EXIT_READ_ERROR)


def _caused_by_interrupt(error: BaseException) -> bool:
    """
    True if a KeyboardInterrupt is behind this exception.

    Ctrl+C that lands inside threading internals (for example while a thread
    is being started) can surface as "RuntimeError: release unlocked lock"
    raised while the KeyboardInterrupt was being handled.
    """
    seen = set()
    current: Optional[BaseException] = error
    while current is not None and id(current) not in seen:
        if isinstance(current, KeyboardInterrupt):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


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
    try:
        # Read bytes and decode as UTF-8 ourselves: the text layer would use
        # the locale's encoding, and would raise a bare UnicodeDecodeError.
        return sys.stdin.buffer.read().decode('utf-8')
    except UnicodeDecodeError as e:
        raise InputError(f'The HTML on stdin is not valid UTF-8: {e}', EXIT_READ_ERROR) from e

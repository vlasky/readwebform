"""
Core orchestration for readwebform.
"""

import sys
import tempfile
from typing import Optional

from .parser import validate_html, inject_csrf_token, wrap_html_fragment
from .forms import FieldSpec, generate_form_html
from .server import FormServer
from .multipart import parse_size_limit
from .output import format_json_output, format_env_output, write_env_file
from .browser import launch_browser


# Exit codes
EXIT_SUCCESS = 0
EXIT_INTERNAL_ERROR = 1
EXIT_INVALID_HTML = 2
EXIT_READ_ERROR = 3
EXIT_BROWSER_LAUNCH_ERROR = 4
EXIT_TIMEOUT = 5
EXIT_UPLOAD_SIZE_EXCEEDED = 6
EXIT_INVALID_ARGUMENT = 7


def run_readwebform(args) -> int:
    """
    Main execution function for readwebform.

    Args:
        args: Parsed command-line arguments

    Returns:
        Exit code
    """
    try:
        # Step 1: Load HTML content
        html = load_html(args)
        if html is None:
            return EXIT_READ_ERROR

        # Step 2: Wrap with title and text if needed
        html = wrap_html_fragment(html, args.title, args.text)

        # Step 3: Validate HTML
        warn_no_submit = not (args.field and not args.no_submit_button)
        valid, error = validate_html(html, warn_no_submit=warn_no_submit)
        if not valid:
            print(f'Error: {error}', file=sys.stderr)
            return EXIT_INVALID_HTML

        # Step 4: Parse size limits
        try:
            max_file_size = parse_size_limit(args.max_file_size) if args.max_file_size else None
        except ValueError as e:
            print(f'Error: Invalid --max-file-size: {e}', file=sys.stderr)
            print('       Use format like: 5M, 200K, 1G, or plain bytes', file=sys.stderr)
            return EXIT_INVALID_ARGUMENT

        try:
            max_total_size = parse_size_limit(args.max_total_size) if args.max_total_size else None
        except ValueError as e:
            print(f'Error: Invalid --max-total-size: {e}', file=sys.stderr)
            print('       Use format like: 20M, 500K, 1G, or plain bytes', file=sys.stderr)
            return EXIT_INVALID_ARGUMENT

        # Step 5: Create server
        server = FormServer(
            html='',  # We'll set this after CSRF injection
            host=args.host,
            port=args.port,
            max_file_size=max_file_size,
            max_total_size=max_total_size,
            timeout=args.timeout,
            reset_timeout_on_error=args.reset_timeout_on_error,
            cert_file=getattr(args, 'cert', None),
            key_file=getattr(args, 'key', None)
        )

        # Step 6: Inject CSRF token into HTML
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        # Step 7: Warn if binding to all interfaces without authentication
        if args.host in ('0.0.0.0', '::'):
            print(
                'Warning: Binding to all interfaces. Form will be accessible from other machines.',
                file=sys.stderr
            )
            print(
                '         Consider using --host 127.0.0.1 for local-only access.',
                file=sys.stderr
            )

        # Step 8: Prepare browser launch callback if requested
        # args.launch_browser: None = not specified, '' = system default, path = custom browser
        browser_path = args.launch_browser

        # Step 9: Start server and wait for submission
        # Browser is launched after server is ready (inside serve() method)
        success, form_data, file_metadata = server.serve(
            launch_browser_path=browser_path
        )

        if not success:
            # Output JSON with error on timeout (unless using env output)
            if not args.print_env:
                json_output = format_json_output({}, {}, success=False, error='timeout')
                print(json_output)
            print('Error: Timeout waiting for submission', file=sys.stderr)
            return EXIT_TIMEOUT

        # Step 9: Output results
        if args.print_env:
            # Print environment variables to stdout
            env_output = format_env_output(form_data.fields)
            print(env_output)
        elif args.envfile:
            # Write to envfile
            write_env_file(args.envfile, form_data.fields)
            # Also output JSON to stdout
            json_output = format_json_output(form_data.fields, file_metadata, success=True)
            print(json_output)
        else:
            # Default: JSON to stdout
            json_output = format_json_output(form_data.fields, file_metadata, success=True)
            print(json_output)

        return EXIT_SUCCESS

    except KeyboardInterrupt:
        print('\n\nInterrupted by user', file=sys.stderr)
        return EXIT_INTERNAL_ERROR
    except Exception as e:
        print(f'Internal error: {e}', file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return EXIT_INTERNAL_ERROR


def load_html(args) -> Optional[str]:
    """
    Load HTML from the appropriate source based on arguments.

    Args:
        args: Parsed command-line arguments

    Returns:
        HTML string or None on error
    """
    try:
        # Priority 1: --html
        if args.html:
            return args.html

        # Priority 2: --htmlfile
        if args.htmlfile:
            with open(args.htmlfile, 'r', encoding='utf-8') as f:
                return f.read()

        # Priority 3: --field (declarative)
        if args.field:
            return generate_html_from_fields(args.field, args.no_submit_button)

        # Priority 4: stdin
        return read_stdin()

    except FileNotFoundError as e:
        print(f'Error: File not found: {e.filename}', file=sys.stderr)
        return None
    except PermissionError as e:
        print(f'Error: Permission denied: {e.filename}', file=sys.stderr)
        return None
    except Exception as e:
        print(f'Error reading input: {e}', file=sys.stderr)
        return None


def generate_html_from_fields(field_specs: list, no_submit_button: bool) -> str:
    """
    Generate HTML from declarative field specifications.

    Args:
        field_specs: List of field specification strings
        no_submit_button: Whether to omit submit button

    Returns:
        Generated HTML
    """
    fields = []
    for spec in field_specs:
        try:
            field = FieldSpec.parse(spec)
            fields.append(field)
        except ValueError as e:
            print(f'Error: {e}', file=sys.stderr)
            sys.exit(EXIT_INVALID_HTML)

    add_submit = not no_submit_button
    return generate_form_html(fields, add_submit_button=add_submit)


def read_stdin() -> str:
    """
    Read HTML from stdin.

    Returns:
        HTML string from stdin
    """
    if sys.stdin.isatty():
        print('Reading HTML from stdin (press Ctrl+D when done)...', file=sys.stderr)

    return sys.stdin.read()

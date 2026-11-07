"""
Ephemeral web server for readwebform.

Lifecycle
---------
``FormServer.serve()`` binds a listening socket, runs a threaded HTTP server
in a background thread, starts a timeout timer, and waits. The first of these
events wins and is recorded as the session's outcome under a lock:

* a valid submission (``success``)
* a cancel-button submission (``cancelled``)
* the timeout timer firing (``timeout``)
* ``shutdown_event`` being set from outside (``stopped``)

Once an outcome is recorded nothing else can change it. A submission that
loses the race receives HTTP 410 and any files it saved are removed. After
the winning handler has finished writing its response it releases the main
thread, which shuts the server down, closes the listener and any lingering
connections, and returns.

Each connection is handled on its own thread with a socket timeout, so an
idle keep-alive connection or a half-sent request can never block the accept
loop. For HTTPS the TLS handshake is performed on the connection thread for
the same reason.
"""

import html
import http.server
import os
import secrets
import socket
import socketserver
import ssl
import sys
import tempfile
import threading
from typing import Optional, cast

from .browser import launch_browser
from .multipart import (
    FormData,
    FormParseError,
    SizeLimitExceeded,
    parse_multipart,
    parse_urlencoded,
    save_uploaded_file,
)

# Maximum request body size when no limit is configured (prevents memory exhaustion)
DEFAULT_MAX_BODY_SIZE = 20 * 1024 * 1024  # 20 MB

# Idle timeout for an accepted connection (seconds). Bounds how long a client
# that has connected but not finished sending a request can hold a thread.
CONNECTION_TIMEOUT = 30.0

OUTCOME_SUCCESS = 'success'
OUTCOME_CANCELLED = 'cancelled'
OUTCOME_TIMEOUT = 'timeout'
OUTCOME_STOPPED = 'stopped'

_QUIET_ERRORS = (ssl.SSLError, socket.timeout, TimeoutError, ConnectionError)


class SessionFinished(RuntimeError):
    """Raised when a handler tries to do work after the session's outcome is set."""


class _FormHTTPServer(http.server.ThreadingHTTPServer):
    """Threaded HTTP server that carries the owning FormServer as instance state."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], session: 'FormServer',
                 ssl_context: Optional[ssl.SSLContext]):
        self.session = session
        self.ssl_context = ssl_context
        self.address_family = socket.AF_INET6 if ':' in address[0] else socket.AF_INET
        self._connections: set[socket.socket] = set()
        self._connections_lock = threading.Lock()
        super().__init__(address, FormServerHandler)

    def server_bind(self):
        # HTTPServer.server_bind() calls socket.getfqdn(), which can stall for
        # seconds on hosts with slow reverse DNS. We never use server_name.
        socketserver.TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])

    def track_connection(self, conn: socket.socket) -> None:
        with self._connections_lock:
            self._connections.add(conn)

    def untrack_connection(self, conn: socket.socket) -> None:
        with self._connections_lock:
            self._connections.discard(conn)

    def server_close(self):
        super().server_close()
        # Close connections whose handlers are still blocked reading, so a
        # browser's idle keep-alive socket does not outlive the session.
        with self._connections_lock:
            conns = list(self._connections)
            self._connections.clear()
        for conn in conns:
            try:
                conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                conn.close()
            except OSError:
                pass

    def handle_error(self, request, client_address):
        exc = sys.exc_info()[1]
        if isinstance(exc, _QUIET_ERRORS):
            # Idle connections timing out, TLS probes, clients going away:
            # routine, not worth a traceback.
            return
        print(f'readwebform: error handling request from {client_address[0]}: {exc!r}',
              file=sys.stderr)


class FormServerHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for form serving and submission."""

    timeout = CONNECTION_TIMEOUT
    server_version = 'readwebform'
    sys_version = ''

    @property
    def form_server(self) -> _FormHTTPServer:
        return cast(_FormHTTPServer, self.server)

    @property
    def session(self) -> 'FormServer':
        return self.form_server.session

    def setup(self):
        ctx = self.form_server.ssl_context
        if ctx is not None:
            # Handshake here, on the connection thread and under the socket
            # timeout, rather than inside accept() on the serving thread.
            # wrap_socket() moves the descriptor to a new SSLSocket, so track
            # that object: it is the one server_close() must close.
            self.request.settimeout(self.timeout)
            self.request = ctx.wrap_socket(self.request, server_side=True,
                                           do_handshake_on_connect=False)
            self.form_server.track_connection(self.request)
            try:
                self.request.do_handshake()
            except BaseException:
                self.form_server.untrack_connection(self.request)
                self.request.close()
                raise
        else:
            self.form_server.track_connection(self.request)
        super().setup()

    def finish(self):
        try:
            super().finish()
        finally:
            self.form_server.untrack_connection(self.request)
            if self.form_server.ssl_context is not None:
                try:
                    self.request.close()
                except OSError:
                    pass

    def log_message(self, format, *args):
        """Suppress default request logging."""

    def do_GET(self):
        """Serve the form."""
        if self.path.split('?', 1)[0] != self.session.endpoint:
            self.send_error(404, 'Not Found')
            return

        body = self.session.html.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        """Process a form submission."""
        session = self.session

        if self.path.split('?', 1)[0] != session.endpoint:
            self.send_error(404, 'Not Found')
            return

        if session.is_finished():
            self._send_error_page(410, 'Gone', 'This form has already been completed')
            return

        content_type = self.headers.get('Content-Type', '')
        length_header = self.headers.get('Content-Length')
        if length_header is None:
            self._send_error_page(411, 'Length Required', 'Content-Length header is required')
            return
        try:
            content_length = int(length_header)
            if content_length < 0:
                raise ValueError('negative')
        except ValueError:
            self._send_error_page(400, 'Bad Request', 'Invalid Content-Length header')
            return

        effective_max = session.max_total_size
        if effective_max is None:
            effective_max = DEFAULT_MAX_BODY_SIZE

        if content_length > effective_max:
            self._send_error_page(
                413, 'Payload Too Large',
                f'Total upload size ({content_length} bytes) exceeds limit ({effective_max} bytes)'
            )
            session.note_recoverable_error()
            return

        try:
            body = self.rfile.read(content_length)
        except OSError as e:
            self._send_error_page(400, 'Bad Request', f'Failed to read request body: {e}')
            return
        if len(body) != content_length:
            self._send_error_page(400, 'Bad Request', 'Request body shorter than Content-Length')
            return

        try:
            if 'multipart/form-data' in content_type:
                form_data = parse_multipart(body, content_type,
                                            session.max_file_size, session.max_total_size)
            else:
                form_data = parse_urlencoded(body)
        except SizeLimitExceeded as e:
            self._send_error_page(413, 'Payload Too Large', str(e))
            session.note_recoverable_error()
            print(f'Upload limit exceeded: {e}', file=sys.stderr)
            return
        except (FormParseError, Exception) as e:
            self._send_error_page(400, 'Bad Request', f'Failed to parse form data: {e}')
            session.note_recoverable_error()
            return

        submitted_token = form_data.fields.pop('_csrf_token', '')
        if isinstance(submitted_token, list) or not secrets.compare_digest(
                str(submitted_token).encode('utf-8'), session.csrf_token.encode('utf-8')):
            self._send_error_page(403, 'Forbidden', 'Invalid CSRF token')
            session.note_recoverable_error()
            return

        if '_cancel' in form_data.fields:
            form_data.fields.pop('_cancel', None)
            if not session.complete(OUTCOME_CANCELLED):
                self._send_error_page(410, 'Gone', 'This form has already been completed')
                return
            try:
                self._send_cancelled_page()
            finally:
                session.release()
            return

        file_metadata: dict = {}
        saved_paths = []
        try:
            for name, uploaded_file in form_data.iter_files():
                filepath = save_uploaded_file(uploaded_file, session.get_upload_dir())
                saved_paths.append(filepath)
                meta = {
                    'filename': uploaded_file.filename,
                    'path': filepath,
                    'size': uploaded_file.size,
                    'content_type': uploaded_file.content_type
                }
                if isinstance(form_data.files[name], list):
                    file_metadata.setdefault(name, []).append(meta)
                else:
                    file_metadata[name] = meta
        except SessionFinished:
            _unlink_all(saved_paths)
            self._send_error_page(410, 'Gone', 'This form has already been completed')
            return
        except Exception as e:
            _unlink_all(saved_paths)
            self._send_error_page(500, 'Internal Server Error', f'Failed to save file: {e}')
            session.note_recoverable_error()
            return

        # Commit the result before responding so a client that disconnects
        # mid-response cannot lose an accepted submission.
        if not session.complete(OUTCOME_SUCCESS, form_data, file_metadata):
            _unlink_all(saved_paths)
            session._cleanup_upload_dir()
            self._send_error_page(410, 'Gone', 'This form has already been completed')
            return

        try:
            self._send_success_page()
        finally:
            session.release()

    def _send_html(self, code: int, page: str):
        body = page.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def _send_success_page(self):
        self._send_html(200, _status_page('Success', 'success', '✅ Form submitted successfully',
                                          'You may now close this window.'))

    def _send_cancelled_page(self):
        self._send_html(200, _status_page('Cancelled', 'cancelled', 'Form submission cancelled',
                                          'You may now close this window.'))

    def _send_error_page(self, code: int, title: str, message: str):
        back = f'<div class="back"><a href="{html.escape(self.session.endpoint)}">← Go back</a></div>'
        self._send_html(code, _status_page(title, 'error', '❌ ' + title, message, back))


def _unlink_all(paths):
    for path in paths:
        try:
            os.unlink(path)
        except OSError:
            pass


def _status_page(title: str, css_class: str, heading: str, message: str, extra: str = '') -> str:
    """Render a minimal status page. All text arguments are escaped."""
    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 600px;
            margin: 100px auto;
            padding: 20px;
            text-align: center;
        }}
        .status {{ font-size: 24px; font-weight: 500; }}
        .success {{ color: #28a745; }}
        .cancelled {{ color: #6c757d; }}
        .error {{ color: #dc3545; }}
        .back {{ margin-top: 20px; }}
        a {{ color: #007bff; text-decoration: none; }}
    </style>
</head>
<body>
    <div class="status {css_class}">{html.escape(heading)}</div>
    <p>{html.escape(message)}</p>
    {extra}
</body>
</html>'''


class FormServer:
    """Ephemeral form server with CSRF protection and timeout management."""

    def __init__(
        self,
        html: str,
        host: str = '127.0.0.1',
        port: Optional[int] = None,
        max_file_size: Optional[int] = None,
        max_total_size: Optional[int] = None,
        timeout: float = 300,
        reset_timeout_on_error: bool = True,
        cert_file: Optional[str] = None,
        key_file: Optional[str] = None,
        upload_dir: Optional[str] = None
    ):
        """
        Initialize form server.

        Args:
            html: HTML content to serve (may be replaced via ``.html`` before serve())
            host: Host to bind to (IPv4 or IPv6 address)
            port: Port to bind to (None = auto-select)
            max_file_size: Maximum individual file size in bytes (None = no limit)
            max_total_size: Maximum total upload size in bytes (None = default cap)
            timeout: Seconds to wait for a submission
            reset_timeout_on_error: Restart the timeout after a recoverable error
            cert_file: Path to SSL certificate file (PEM format)
            key_file: Path to SSL private key file (PEM format)
            upload_dir: Directory for uploaded files (None = a temporary
                directory created on first upload and removed if left empty)
        """
        self.html = html
        self.host = host
        self.port = port or 0  # 0 = auto-select free port
        self.max_file_size = max_file_size
        self.max_total_size = max_total_size
        self.timeout = timeout
        self.reset_timeout_on_error = reset_timeout_on_error
        if (cert_file is None) != (key_file is None):
            raise ValueError('cert_file and key_file must be given together')
        self.cert_file = cert_file
        self.key_file = key_file
        self.use_ssl = cert_file is not None and key_file is not None

        self.csrf_token = secrets.token_hex(16)
        self.endpoint = '/readform_' + secrets.token_hex(8)

        self.upload_dir: Optional[str] = upload_dir
        self._created_upload_dir = False

        # Outcome state, guarded by _lock
        self._lock = threading.Lock()
        self._outcome: Optional[str] = None
        self.form_data: Optional[FormData] = None
        self.file_metadata: dict = {}
        self.timeout_timer: Optional[threading.Timer] = None
        self._timer_generation = 0

        # Set when serve() should stop waiting. Setting it from outside stops
        # the server (outcome 'stopped').
        self.shutdown_event = threading.Event()

        self.server: Optional[_FormHTTPServer] = None
        self._url = ''

    # -- outcome ---------------------------------------------------------

    @property
    def outcome(self) -> Optional[str]:
        return self._outcome

    @property
    def success(self) -> bool:
        return self._outcome == OUTCOME_SUCCESS

    @property
    def cancelled(self) -> bool:
        return self._outcome == OUTCOME_CANCELLED

    @property
    def timed_out(self) -> bool:
        return self._outcome == OUTCOME_TIMEOUT

    def is_finished(self) -> bool:
        return self._outcome is not None or self.shutdown_event.is_set()

    def complete(self, outcome: str, form_data: Optional[FormData] = None,
                 file_metadata: Optional[dict] = None) -> bool:
        """
        Record the session outcome. Only the first call succeeds.

        Returns:
            True if this call recorded the outcome, False if one was already set.
        """
        with self._lock:
            return self._complete_locked(outcome, form_data, file_metadata)

    def _complete_locked(self, outcome: str, form_data: Optional[FormData],
                         file_metadata: Optional[dict]) -> bool:
        if self._outcome is not None or self.shutdown_event.is_set():
            return False
        self._outcome = outcome
        self.form_data = form_data
        self.file_metadata = file_metadata or {}
        if self.timeout_timer is not None:
            self.timeout_timer.cancel()
            self.timeout_timer = None
        return True

    def release(self) -> None:
        """Let serve() return. Called after the final response has been written."""
        self.shutdown_event.set()

    # -- timeout ---------------------------------------------------------

    def _start_timer(self) -> None:
        with self._lock:
            if self.timeout_timer is not None:
                self.timeout_timer.cancel()
                self.timeout_timer = None
            if self._outcome is not None:
                return
            # Timer.cancel() cannot stop a callback that has already started
            # and is waiting for the lock, so each timer carries a generation
            # and a stale callback is ignored.
            self._timer_generation += 1
            self.timeout_timer = threading.Timer(self.timeout, self._on_timeout,
                                                 args=(self._timer_generation,))
            self.timeout_timer.daemon = True
            self.timeout_timer.start()

    def reset_timeout(self) -> None:
        """Restart the timeout timer (used after recoverable errors)."""
        if self.reset_timeout_on_error:
            self._start_timer()

    def note_recoverable_error(self) -> None:
        self.reset_timeout()

    def _on_timeout(self, generation: Optional[int] = None) -> None:
        with self._lock:
            if generation is not None and generation != self._timer_generation:
                return  # a reset replaced this timer while its callback was pending
            completed = self._complete_locked(OUTCOME_TIMEOUT, None, None)
        if completed:
            self.release()

    # -- uploads ---------------------------------------------------------

    def get_upload_dir(self) -> str:
        """Return the upload directory, creating a temporary one on first use."""
        with self._lock:
            if self._outcome is not None:
                raise SessionFinished('session already completed')
            if self.upload_dir is None:
                self.upload_dir = tempfile.mkdtemp(prefix='readwebform_')
                self._created_upload_dir = True
            elif not os.path.isdir(self.upload_dir):
                os.makedirs(self.upload_dir, mode=0o700, exist_ok=True)
            return self.upload_dir

    def _cleanup_upload_dir(self) -> None:
        if self._created_upload_dir and self.upload_dir:
            try:
                os.rmdir(self.upload_dir)  # only succeeds if empty
            except OSError:
                pass

    # -- serving ---------------------------------------------------------

    def _build_ssl_context(self) -> ssl.SSLContext:
        assert self.cert_file is not None and self.key_file is not None
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        try:
            context.load_cert_chain(certfile=self.cert_file, keyfile=self.key_file)
        except ssl.SSLError as e:
            print(f'Error: SSL configuration failed: {e}', file=sys.stderr)
            raise
        except FileNotFoundError as e:
            print(f'Error: SSL certificate or key file not found: {e}', file=sys.stderr)
            raise
        return context

    def _build_url(self, port: int) -> str:
        protocol = 'https' if self.use_ssl else 'http'
        url_host = f'[{self.host}]' if ':' in self.host else self.host
        return f'{protocol}://{url_host}:{port}{self.endpoint}'

    def serve(self, launch_browser_path: Optional[str] = None,
              url_file: Optional[str] = None) -> tuple[bool, Optional[FormData], dict, bool]:
        """
        Start the server and wait for a submission, cancellation, or timeout.

        Args:
            launch_browser_path: If not None, launch a browser once the server
                is ready. Empty string means the system default browser.
            url_file: If provided, write the form URL to this file.

        Returns:
            Tuple of (success, form_data, file_metadata, cancelled)

        Raises:
            OSError: If the port is already in use or binding fails
            ssl.SSLError: If the SSL certificate or key is invalid
        """
        ssl_context = self._build_ssl_context() if self.use_ssl else None

        try:
            self.server = _FormHTTPServer((self.host, self.port), self, ssl_context)
        except OSError as e:
            print(f'Error: Failed to bind to {self.host}:{self.port}: {e}', file=sys.stderr)
            raise

        self._url = self._build_url(self.server.server_address[1])
        server_thread = threading.Thread(target=self._run_server, daemon=True)
        started = False

        try:
            print(f'\nOpen this URL in your browser:\n  {self._url}\n', file=sys.stderr)

            if url_file:
                with open(url_file, 'w', encoding='utf-8') as f:
                    f.write(self._url + '\n')

            # Serve before launching the browser: some launchers block until
            # the page has loaded.
            server_thread.start()
            started = True
            self._start_timer()

            if launch_browser_path is not None:
                launch_browser(self._url, launch_browser_path or None)

            # Poll rather than wait() so Ctrl+C is honoured on every platform
            while not self.shutdown_event.wait(0.5):
                pass
        finally:
            with self._lock:
                if self.timeout_timer is not None:
                    self.timeout_timer.cancel()
                    self.timeout_timer = None
                if self._outcome is None:
                    self._outcome = OUTCOME_STOPPED
            self.shutdown_event.set()
            if started:
                self.server.shutdown()
            self.server.server_close()
            if started:
                server_thread.join(timeout=2.0)
            self._cleanup_upload_dir()

        if self.success:
            return True, self.form_data, self.file_metadata, False
        return False, None, {}, self.cancelled

    def _run_server(self) -> None:
        server = self.server
        assert server is not None
        try:
            server.serve_forever(poll_interval=0.25)
        except OSError as e:
            if not self.shutdown_event.is_set():
                print(f'Server error: {e}', file=sys.stderr)

    def get_url(self) -> str:
        """Return the form URL, or '' before the server has bound."""
        return self._url

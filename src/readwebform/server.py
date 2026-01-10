"""
Ephemeral web server for readwebform.
"""

import html
import http.server
import secrets
import socketserver
import ssl
import sys
import tempfile
import threading
import time
from typing import Optional, Callable, Tuple
from urllib.parse import urlparse, parse_qs

from .multipart import parse_multipart, parse_urlencoded, FormData, save_uploaded_file
from .browser import launch_browser


# Maximum request body size when no limit is configured (prevents memory exhaustion)
DEFAULT_MAX_BODY_SIZE = 20 * 1024 * 1024  # 20 MB


class FormServerHandler(http.server.BaseHTTPRequestHandler):
    """HTTP request handler for form serving and submission."""

    # Class variables set by server
    html_content: str = ''
    csrf_token: str = ''
    endpoint: str = ''
    max_file_size: Optional[int] = None
    max_total_size: Optional[int] = None
    on_success: Optional[Callable] = None
    form_data: Optional[FormData] = None
    upload_dir: str = ''
    reset_timeout_callback: Optional[Callable] = None
    server_instance: Optional[socketserver.TCPServer] = None

    def log_message(self, format, *args):
        """Suppress default logging to stdout."""
        # We'll log errors to stderr when needed
        pass

    def do_GET(self):
        """Handle GET requests - serve the form."""
        parsed = urlparse(self.path)

        if parsed.path == self.endpoint:
            # Serve the form
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Cache-Control', 'no-store, no-cache, must-revalidate')
            self.end_headers()
            self.wfile.write(self.html_content.encode('utf-8'))
        else:
            # 404 for other paths
            self.send_error(404, 'Not Found')

    def do_POST(self):
        """Handle POST requests - process form submission."""
        parsed = urlparse(self.path)

        if parsed.path != self.endpoint:
            self.send_error(404, 'Not Found')
            return

        # Get Content-Type and Content-Length
        content_type = self.headers.get('Content-Type', '')
        try:
            content_length = int(self.headers.get('Content-Length', 0))
        except (ValueError, TypeError):
            self._send_error_page(400, 'Bad Request', 'Invalid Content-Length header')
            return

        # Use configured limit or fall back to default max body size
        effective_max = self.max_total_size if self.max_total_size else DEFAULT_MAX_BODY_SIZE

        # Check total size limit before reading
        if content_length > effective_max:
            self._send_error_page(
                413,
                'Payload Too Large',
                f'Total upload size ({content_length} bytes) exceeds limit ({effective_max} bytes)'
            )
            if self.reset_timeout_callback:
                self.reset_timeout_callback()
            return

        # Read request body
        try:
            body = self.rfile.read(content_length)
        except Exception as e:
            self._send_error_page(500, 'Internal Server Error', f'Failed to read request: {e}')
            return

        # Parse form data
        try:
            if 'multipart/form-data' in content_type:
                form_data = parse_multipart(
                    body, content_type,
                    self.max_file_size,
                    self.max_total_size
                )
            else:
                form_data = parse_urlencoded(body)
        except ValueError as e:
            # Size limit exceeded
            self._send_error_page(413, 'Payload Too Large', str(e))
            if self.reset_timeout_callback:
                self.reset_timeout_callback()
            print(f'Upload limit exceeded: {e}', file=sys.stderr)
            return
        except Exception as e:
            self._send_error_page(400, 'Bad Request', f'Failed to parse form data: {e}')
            if self.reset_timeout_callback:
                self.reset_timeout_callback()
            return

        # Validate CSRF token
        submitted_token = form_data.fields.get('_csrf_token', '')
        if submitted_token != self.csrf_token:
            self._send_error_page(403, 'Forbidden', 'Invalid CSRF token')
            if self.reset_timeout_callback:
                self.reset_timeout_callback()
            return

        # Remove CSRF token from fields
        form_data.fields.pop('_csrf_token', None)

        # Save uploaded files
        file_metadata = {}
        for name, uploaded_file in form_data.files.items():
            try:
                filepath = save_uploaded_file(uploaded_file, self.upload_dir)
                file_metadata[name] = {
                    'filename': uploaded_file.filename,
                    'path': filepath,
                    'size': uploaded_file.size,
                    'content_type': uploaded_file.content_type
                }
            except Exception as e:
                self._send_error_page(500, 'Internal Server Error', f'Failed to save file: {e}')
                return

        # Store form data for retrieval
        self.form_data = form_data
        self.__class__.form_data = form_data

        # Send success response
        self._send_success_page()

        # Notify success callback - must be done AFTER response is fully sent
        if self.on_success:
            self.on_success(form_data, file_metadata)

        # Schedule shutdown from a separate thread to avoid blocking the handler
        # This ensures the HTTP response is fully sent before shutdown begins
        def delayed_shutdown():
            time.sleep(0.05)  # Brief delay to ensure response is sent (50ms)
            if hasattr(self, 'server_instance') and self.server_instance:
                try:
                    self.server_instance.shutdown()
                except:
                    pass  # Shutdown might be called multiple times, ignore errors

        shutdown_thread = threading.Thread(target=delayed_shutdown, daemon=True)
        shutdown_thread.start()

    def _send_success_page(self):
        """Send success confirmation page."""
        success_html = '''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>Success</title>
    <style>
        body {
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 600px;
            margin: 100px auto;
            padding: 20px;
            text-align: center;
        }
        .success {
            color: #28a745;
            font-size: 24px;
            font-weight: 500;
        }
    </style>
</head>
<body>
    <div class="success">✅ Form submitted successfully</div>
    <p>You may now close this window.</p>
</body>
</html>'''
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(success_html.encode('utf-8'))

    def _send_error_page(self, code: int, title: str, message: str):
        """Send error page to user."""
        # Escape HTML to prevent XSS attacks from malicious filenames or error messages
        safe_title = html.escape(title)
        safe_message = html.escape(message)
        error_html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>{safe_title}</title>
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 600px;
            margin: 100px auto;
            padding: 20px;
            text-align: center;
        }}
        .error {{
            color: #dc3545;
            font-size: 24px;
            font-weight: 500;
        }}
        .back {{
            margin-top: 20px;
        }}
        a {{
            color: #007bff;
            text-decoration: none;
        }}
    </style>
</head>
<body>
    <div class="error">❌ {safe_title}</div>
    <p>{safe_message}</p>
    <div class="back"><a href="{self.endpoint}">← Go back</a></div>
</body>
</html>'''
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(error_html.encode('utf-8'))


class FormServer:
    """Ephemeral form server with CSRF protection and timeout management."""

    def __init__(
        self,
        html: str,
        host: str = '127.0.0.1',
        port: Optional[int] = None,
        max_file_size: Optional[int] = None,
        max_total_size: Optional[int] = None,
        timeout: int = 300,
        reset_timeout_on_error: bool = True,
        cert_file: Optional[str] = None,
        key_file: Optional[str] = None
    ):
        """
        Initialize form server.

        Args:
            html: HTML content to serve
            host: Host to bind to
            port: Port to bind to (None = auto-select)
            max_file_size: Maximum individual file size
            max_total_size: Maximum total upload size
            timeout: Timeout in seconds
            reset_timeout_on_error: Whether to reset timeout on errors
            cert_file: Path to SSL certificate file (PEM format)
            key_file: Path to SSL private key file (PEM format)
        """
        self.html = html
        self.host = host
        self.port = port or 0  # 0 = auto-select free port
        self.max_file_size = max_file_size
        self.max_total_size = max_total_size
        self.timeout = timeout
        self.reset_timeout_on_error = reset_timeout_on_error
        self.cert_file = cert_file
        self.key_file = key_file
        self.use_ssl = cert_file is not None and key_file is not None

        # Generate CSRF token and endpoint
        self.csrf_token = secrets.token_hex(16)
        self.endpoint = '/readform_' + secrets.token_hex(8)

        # Create upload directory
        self.upload_dir = tempfile.mkdtemp(prefix='readwebform_')

        # State
        self.server: Optional[socketserver.TCPServer] = None
        self.form_data: Optional[FormData] = None
        self.file_metadata: dict = {}
        self.success = False
        self.timed_out = False
        self.shutdown_event = threading.Event()  # Thread-safe shutdown signaling
        self.timeout_timer: Optional[threading.Timer] = None

    def reset_timeout(self):
        """Reset the timeout timer."""
        # Cancel existing timer
        if self.timeout_timer:
            self.timeout_timer.cancel()

        # Start new timer
        if self.reset_timeout_on_error:
            self.timeout_timer = threading.Timer(self.timeout, self._on_timeout)
            self.timeout_timer.daemon = True
            self.timeout_timer.start()

    def _on_timeout(self):
        """Callback when timeout expires."""
        self.timed_out = True
        if self.server:
            # Set shutdown flag so serve_forever() will exit
            self.server._BaseServer__shutdown_request = True
            try:
                # Close the socket to immediately unblock any waiting accept()
                self.server.socket.close()
            except:
                pass
        self.shutdown_event.set()

    def _on_success(self, form_data: FormData, file_metadata: dict):
        """Callback when form is successfully submitted."""
        # Cancel timeout timer
        if self.timeout_timer:
            self.timeout_timer.cancel()

        self.form_data = form_data
        self.file_metadata = file_metadata
        self.success = True
        self.shutdown_event.set()  # Signal shutdown to main thread

    def serve(self, launch_browser_path: Optional[str] = None) -> Tuple[bool, Optional[FormData], dict]:
        """
        Start server and wait for submission or timeout.

        Args:
            launch_browser_path: If not None, launch browser after server is ready.
                                 Empty string means system default, path means custom browser.

        Returns:
            Tuple of (success, form_data, file_metadata)

        Raises:
            OSError: If the port is already in use or binding fails
            ssl.SSLError: If SSL certificate/key is invalid
        """
        # Create custom server class with allow_reuse_address set BEFORE binding
        class ReusableTCPServer(socketserver.TCPServer):
            allow_reuse_address = True

        try:
            self.server = ReusableTCPServer((self.host, self.port), FormServerHandler)
        except OSError as e:
            # Port already in use or binding failed
            print(f'Error: Failed to bind to {self.host}:{self.port}: {e}', file=sys.stderr)
            raise

        # Wrap socket with SSL if configured
        if self.use_ssl:
            try:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                context.load_cert_chain(certfile=self.cert_file, keyfile=self.key_file)
                self.server.socket = context.wrap_socket(
                    self.server.socket,
                    server_side=True
                )
            except ssl.SSLError as e:
                print(f'Error: SSL configuration failed: {e}', file=sys.stderr)
                self.server.server_close()
                raise
            except FileNotFoundError as e:
                print(f'Error: SSL certificate or key file not found: {e}', file=sys.stderr)
                self.server.server_close()
                raise

        # Set socket timeout to allow shutdown to work even with keep-alive connections
        self.server.socket.settimeout(1.0)

        # Get actual port and determine protocol
        actual_port = self.server.server_address[1]
        protocol = 'https' if self.use_ssl else 'http'

        # Format host for URL (IPv6 addresses need brackets)
        if ':' in self.host:
            # IPv6 address - wrap in brackets for URL
            url_host = f'[{self.host}]'
        else:
            url_host = self.host
        url = f'{protocol}://{url_host}:{actual_port}{self.endpoint}'

        # Configure handler class variables
        FormServerHandler.html_content = self.html
        FormServerHandler.csrf_token = self.csrf_token
        FormServerHandler.endpoint = self.endpoint
        FormServerHandler.max_file_size = self.max_file_size
        FormServerHandler.max_total_size = self.max_total_size
        FormServerHandler.on_success = self._on_success
        FormServerHandler.upload_dir = self.upload_dir
        FormServerHandler.server_instance = self.server
        if self.reset_timeout_on_error:
            FormServerHandler.reset_timeout_callback = self.reset_timeout
        else:
            FormServerHandler.reset_timeout_callback = None

        # Print URL to stderr
        print(f'\nOpen this URL in your browser:', file=sys.stderr)
        print(f'  {url}\n', file=sys.stderr)

        # Launch browser AFTER server is bound and ready (fixes race condition)
        if launch_browser_path is not None:
            # Empty string means use system default, non-empty means custom path
            browser_path = launch_browser_path if launch_browser_path else None
            launch_browser(url, browser_path)

        # Start timeout timer
        self.timeout_timer = threading.Timer(self.timeout, self._on_timeout)
        self.timeout_timer.daemon = True
        self.timeout_timer.start()

        # Start server in background thread
        server_thread = threading.Thread(target=self._run_server, daemon=True)
        server_thread.start()

        # Wait for either success or timeout to set shutdown_event
        self.shutdown_event.wait()

        # Wait for server thread to finish
        server_thread.join(timeout=2.0)

        # If still alive after join timeout, force-close the socket
        if server_thread.is_alive():
            try:
                self.server.server_close()
            except:
                pass

        # Return results based on what happened
        if self.timed_out:
            return False, None, {}
        else:
            return self.success, self.form_data, self.file_metadata

    def _run_server(self):
        """Run server in thread."""
        try:
            self.server.serve_forever()
        except OSError as e:
            # Socket timeout or closed socket during shutdown - this is expected
            # Don't print error for normal shutdown scenarios
            if not self.shutdown_event.is_set() and not self.timed_out:
                print(f'Server error: {e}', file=sys.stderr)
        except Exception as e:
            print(f'Server error: {e}', file=sys.stderr)

    def get_url(self) -> str:
        """Get the server URL."""
        if self.server:
            actual_port = self.server.server_address[1]
            protocol = 'https' if self.use_ssl else 'http'
            # Format host for URL (IPv6 addresses need brackets)
            if ':' in self.host:
                url_host = f'[{self.host}]'
            else:
                url_host = self.host
            return f'{protocol}://{url_host}:{actual_port}{self.endpoint}'
        return ''

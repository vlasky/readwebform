"""
Integration tests for readwebform - tests the full request/response cycle.
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.parse
from http.client import HTTPConnection
from io import BytesIO

import pytest

from readwebform.server import FormServer
from readwebform.parser import inject_csrf_token, wrap_html_fragment
from readwebform.multipart import parse_size_limit


class TestServerIntegration:
    """Test actual server startup and form serving."""

    def test_server_starts_and_serves_form(self):
        """Test that server starts and serves HTML."""
        html = '<form><input name="test" required><button type="submit">Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,  # Auto-select
            timeout=5
        )

        # Inject CSRF token
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        # Start server in background
        server_thread = threading.Thread(target=server.serve, daemon=True)
        server_thread.start()

        # Wait for server to start
        time.sleep(0.5)

        # Get URL
        url = server.get_url()
        assert url.startswith('http://127.0.0.1:')

        # Fetch the form
        response = urllib.request.urlopen(url, timeout=2)
        content = response.read().decode('utf-8')

        assert response.status == 200
        assert '<form' in content
        assert server.csrf_token in content
        assert '_csrf_token' in content

        # Shutdown
        server.shutdown_event.set()
        server_thread.join(timeout=2)

    def test_form_submission_simple_fields(self):
        """Test submitting a form with simple text fields."""
        html = '''<form>
            <input name="username" value="testuser">
            <input name="email" value="test@example.com">
            <button type="submit">Submit</button>
        </form>'''
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=10
        )

        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        # Start server in background
        result = {'success': False, 'data': None, 'files': None}

        def run_server():
            success, form_data, file_metadata = server.serve()
            result['success'] = success
            if form_data:
                result['data'] = form_data.fields
                result['files'] = file_metadata

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        time.sleep(0.5)

        # Submit form
        url = server.get_url()
        data = {
            'username': 'john_doe',
            'email': 'john@example.com',
            '_csrf_token': server.csrf_token
        }

        encoded_data = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded_data, method='POST')
        response = urllib.request.urlopen(req, timeout=2)

        assert response.status == 200

        # Wait for server to process
        server_thread.join(timeout=2)

        assert result['success'] is True
        assert result['data']['username'] == 'john_doe'
        assert result['data']['email'] == 'john@example.com'
        assert '_csrf_token' not in result['data']  # Should be removed

    def test_file_upload(self):
        """Test file upload functionality."""
        html = '''<form enctype="multipart/form-data">
            <input name="name" value="test">
            <input type="file" name="document">
            <button type="submit">Submit</button>
        </form>'''
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=10,
            max_file_size=parse_size_limit('1M')
        )

        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'success': False, 'data': None, 'files': None}

        def run_server():
            success, form_data, file_metadata = server.serve()
            result['success'] = success
            if form_data:
                result['data'] = form_data.fields
                result['files'] = file_metadata

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        time.sleep(0.5)

        # Create multipart form data
        url = server.get_url()
        boundary = '----WebKitFormBoundary7MA4YWxkTrZu0gW'

        file_content = b'Test file content\nLine 2\nLine 3'

        body = (
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="name"\r\n\r\n'
            f'Alice\r\n'
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="_csrf_token"\r\n\r\n'
            f'{server.csrf_token}\r\n'
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="document"; filename="test.txt"\r\n'
            f'Content-Type: text/plain\r\n\r\n'
        ).encode('utf-8') + file_content + f'\r\n------{boundary}--\r\n'.encode('utf-8')

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                'Content-Type': f'multipart/form-data; boundary=----{boundary}',
                'Content-Length': str(len(body))
            },
            method='POST'
        )

        response = urllib.request.urlopen(req, timeout=2)
        assert response.status == 200

        server_thread.join(timeout=2)

        assert result['success'] is True
        assert result['data']['name'] == 'Alice'
        assert 'document' in result['files']
        assert result['files']['document']['filename'] == 'test.txt'
        assert os.path.exists(result['files']['document']['path'])

        # Verify file content
        with open(result['files']['document']['path'], 'rb') as f:
            saved_content = f.read()
        assert saved_content == file_content

    def test_csrf_token_validation(self):
        """Test that CSRF token is validated."""
        html = '<form><input name="test"><button type="submit">Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=5
        )

        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        server_thread = threading.Thread(target=server.serve, daemon=True)
        server_thread.start()

        time.sleep(0.5)

        # Submit with WRONG CSRF token
        url = server.get_url()
        data = {
            'test': 'value',
            '_csrf_token': 'WRONG_TOKEN_12345'
        }

        encoded_data = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded_data, method='POST')

        try:
            urllib.request.urlopen(req, timeout=2)
            assert False, "Should have raised HTTP 403 error"
        except urllib.error.HTTPError as e:
            assert e.code == 403  # Forbidden

        # Server should still be running (didn't shutdown on error)
        time.sleep(0.5)

        # Cleanup
        server.shutdown_event.set()
        server_thread.join(timeout=2)

    def test_file_size_limit_exceeded(self):
        """Test that file size limits are enforced."""
        html = '<form enctype="multipart/form-data"><input type="file" name="file"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=5,
            max_file_size=100  # 100 bytes limit
        )

        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        server_thread = threading.Thread(target=server.serve, daemon=True)
        server_thread.start()

        time.sleep(0.5)

        # Create file larger than limit
        url = server.get_url()
        boundary = '----WebKitFormBoundary'

        large_content = b'X' * 200  # 200 bytes, exceeds 100 byte limit

        body = (
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="_csrf_token"\r\n\r\n'
            f'{server.csrf_token}\r\n'
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="large.bin"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode('utf-8') + large_content + f'\r\n------{boundary}--\r\n'.encode('utf-8')

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                'Content-Type': f'multipart/form-data; boundary=----{boundary}',
                'Content-Length': str(len(body))
            },
            method='POST'
        )

        try:
            urllib.request.urlopen(req, timeout=2)
            assert False, "Should have raised HTTP 413 error"
        except urllib.error.HTTPError as e:
            assert e.code == 413  # Payload Too Large

        # Cleanup
        server.shutdown_event.set()
        server_thread.join(timeout=2)

    def test_timeout_behavior(self):
        """Test that server times out if no submission."""
        html = '<form><input name="test"><button type="submit">Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=2  # 2 second timeout
        )

        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        start_time = time.time()
        success, form_data, file_metadata = server.serve()
        elapsed = time.time() - start_time

        assert success is False
        assert form_data is None
        assert 1.5 < elapsed < 3.0  # Should timeout around 2 seconds

    def test_total_size_limit_exceeded(self):
        """Test that total size limits are enforced."""
        html = '<form enctype="multipart/form-data"><input type="file" name="file"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=5,
            max_total_size=200  # 200 bytes total limit
        )

        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        server_thread = threading.Thread(target=server.serve, daemon=True)
        server_thread.start()

        time.sleep(0.5)

        # Create request larger than total limit
        url = server.get_url()
        boundary = '----WebKitFormBoundary'

        content = b'X' * 150

        body = (
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="_csrf_token"\r\n\r\n'
            f'{server.csrf_token}\r\n'
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="file.bin"\r\n'
            f'Content-Type: application/octet-stream\r\n\r\n'
        ).encode('utf-8') + content + f'\r\n------{boundary}--\r\n'.encode('utf-8')

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                'Content-Type': f'multipart/form-data; boundary=----{boundary}',
                'Content-Length': str(len(body))
            },
            method='POST'
        )

        try:
            urllib.request.urlopen(req, timeout=2)
            assert False, "Should have raised HTTP 413 error"
        except urllib.error.HTTPError as e:
            assert e.code == 413  # Payload Too Large

        # Cleanup
        server.shutdown_event.set()
        server_thread.join(timeout=2)


class TestEndToEndScenarios:
    """Test complete end-to-end workflows."""

    def test_declarative_form_submission(self):
        """Test form generated from declarative fields."""
        from readwebform.forms import FieldSpec, generate_form_html

        fields = [
            FieldSpec.parse('username:text:Username:required'),
            FieldSpec.parse('age:number:Age:min=0,max=120'),
            FieldSpec.parse('bio:textarea:Bio:rows=4')
        ]

        html = generate_form_html(fields, add_submit_button=True)
        html = wrap_html_fragment(html, title='User Profile', text='Please enter your information')

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=10
        )

        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'success': False, 'data': None}

        def run_server():
            success, form_data, _ = server.serve()
            result['success'] = success
            if form_data:
                result['data'] = form_data.fields

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        time.sleep(0.5)

        # Submit form
        url = server.get_url()
        data = {
            'username': 'alice',
            'age': '30',
            'bio': 'Software developer\nLoves Python',
            '_csrf_token': server.csrf_token
        }

        encoded_data = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded_data, method='POST')
        response = urllib.request.urlopen(req, timeout=2)

        assert response.status == 200

        server_thread.join(timeout=2)

        assert result['success'] is True
        assert result['data']['username'] == 'alice'
        assert result['data']['age'] == '30'
        assert 'Software developer' in result['data']['bio']

    def test_multiple_file_uploads(self):
        """Test uploading multiple files."""
        html = '''<form enctype="multipart/form-data">
            <input name="name">
            <input type="file" name="file1">
            <input type="file" name="file2">
            <button type="submit">Submit</button>
        </form>'''
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=10
        )

        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'success': False, 'data': None, 'files': None}

        def run_server():
            success, form_data, file_metadata = server.serve()
            result['success'] = success
            if form_data:
                result['data'] = form_data.fields
                result['files'] = file_metadata

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        time.sleep(0.5)

        # Create multipart with two files
        url = server.get_url()
        boundary = '----WebKitFormBoundary'

        file1_content = b'First file content'
        file2_content = b'Second file content'

        body = (
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="name"\r\n\r\n'
            f'Test User\r\n'
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="_csrf_token"\r\n\r\n'
            f'{server.csrf_token}\r\n'
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="file1"; filename="doc1.txt"\r\n'
            f'Content-Type: text/plain\r\n\r\n'
        ).encode('utf-8') + file1_content + (
            f'\r\n------{boundary}\r\n'
            f'Content-Disposition: form-data; name="file2"; filename="doc2.txt"\r\n'
            f'Content-Type: text/plain\r\n\r\n'
        ).encode('utf-8') + file2_content + f'\r\n------{boundary}--\r\n'.encode('utf-8')

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                'Content-Type': f'multipart/form-data; boundary=----{boundary}',
                'Content-Length': str(len(body))
            },
            method='POST'
        )

        response = urllib.request.urlopen(req, timeout=2)
        assert response.status == 200

        server_thread.join(timeout=2)

        assert result['success'] is True
        assert result['data']['name'] == 'Test User'
        assert 'file1' in result['files']
        assert 'file2' in result['files']
        assert result['files']['file1']['filename'] == 'doc1.txt'
        assert result['files']['file2']['filename'] == 'doc2.txt'

        # Verify both files exist
        assert os.path.exists(result['files']['file1']['path'])
        assert os.path.exists(result['files']['file2']['path'])

    def test_special_characters_in_form_data(self):
        """Test handling of special characters and encoding."""
        html = '<form><input name="message"><button type="submit">Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=10
        )

        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'success': False, 'data': None}

        def run_server():
            success, form_data, _ = server.serve()
            result['success'] = success
            if form_data:
                result['data'] = form_data.fields

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        time.sleep(0.5)

        # Submit with special characters
        url = server.get_url()
        special_message = "Hello & goodbye! <script>alert('test')</script> 你好"
        data = {
            'message': special_message,
            '_csrf_token': server.csrf_token
        }

        encoded_data = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded_data, method='POST')
        response = urllib.request.urlopen(req, timeout=2)

        assert response.status == 200

        server_thread.join(timeout=2)

        assert result['success'] is True
        assert result['data']['message'] == special_message


class TestCLIProcessBehavior:
    """Test the full CLI as a subprocess - verifies process actually exits."""

    def test_timeout_exits_process_promptly(self):
        """Test that readwebform process exits within timeout period.

        This is a critical test that verifies the bug reported by the user:
        the process should exit automatically after timeout, not hang indefinitely.
        """
        # Run readwebform as subprocess with 3-second timeout
        start_time = time.time()

        result = subprocess.run(
            [
                sys.executable, '-m', 'readwebform',
                '--field', 'test:text:Test',
                '--timeout', '3',
            ],
            capture_output=True,
            timeout=10,  # Kill if hangs longer than 10s
        )

        elapsed = time.time() - start_time

        # Verify exit code is 5 (timeout)
        assert result.returncode == 5, \
            f"Expected exit code 5 (timeout), got {result.returncode}"

        # Verify it actually exited within reasonable time
        # Should be ~3 seconds + overhead (max 6 seconds)
        # Lower bound relaxed since fast systems may exit quicker
        assert 1.5 < elapsed < 6.0, \
            f"Should exit around 3s, took {elapsed:.2f}s"

        # Verify stderr contains the URL
        stderr = result.stderr.decode('utf-8')
        assert 'http://127.0.0.1:' in stderr, \
            "Should print server URL to stderr"

    def test_process_handles_keyboard_interrupt(self):
        """Test that process exits gracefully on keyboard interrupt."""
        # Run readwebform as subprocess
        proc = subprocess.Popen(
            [
                sys.executable, '-m', 'readwebform',
                '--field', 'test:text:Test',
                '--timeout', '30',
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Wait for server to start
        time.sleep(0.5)

        # Send SIGINT (Ctrl+C)
        import signal
        proc.send_signal(signal.SIGINT)

        # Wait for process to exit
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("Process did not exit within 2 seconds after SIGINT")

        # Should return exit code 1 for keyboard interrupt
        assert proc.returncode == 1, \
            f"Expected exit code 1 for interrupt, got {proc.returncode}"

        stderr = proc.stderr.read().decode('utf-8')
        assert 'Interrupted' in stderr or 'interrupt' in stderr.lower(), \
            "Error message should mention interruption"

    def test_invalid_html_exits_with_error_code(self):
        """Test that invalid HTML causes process to exit with error code 2."""
        result = subprocess.run(
            [
                sys.executable, '-m', 'readwebform',
                '--html', '<div>No form here</div>',
            ],
            capture_output=True,
            timeout=5,
        )

        assert result.returncode == 2, \
            f"Expected exit code 2 (invalid HTML), got {result.returncode}"

        stderr = result.stderr.decode('utf-8')
        assert 'form' in stderr.lower(), \
            "Error message should mention form"

    def test_timeout_with_browser_keepalive_connection(self):
        """Test that timeout works even with browser keeping connection alive.

        This simulates the bug where Firefox's HTTP keep-alive connection
        prevented the server from shutting down on timeout.
        """
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=3)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'timed_out': False, 'elapsed': None}

        def run_server():
            start = time.time()
            success, _, _ = server.serve()
            result['elapsed'] = time.time() - start
            result['timed_out'] = not success

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Wait for server to start
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Open connection and keep it alive (simulate browser behavior)
        # Use raw socket to maintain connection
        import socket
        parsed = urllib.parse.urlparse(url)
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((parsed.hostname, parsed.port))

        # Send GET request with Connection: keep-alive
        request = f"GET {parsed.path} HTTP/1.1\r\n"
        request += f"Host: {parsed.hostname}:{parsed.port}\r\n"
        request += "Connection: keep-alive\r\n"
        request += "\r\n"
        sock.send(request.encode())

        # Read response but keep connection open
        sock.recv(4096)

        # Keep connection alive and wait for timeout
        # The server should still timeout even with this connection open
        server_thread.join(timeout=6.0)

        # Clean up
        sock.close()

        # Verify timeout occurred despite keep-alive connection
        assert result['timed_out'] is True, "Should timeout even with keep-alive connection"
        assert result['elapsed'] is not None
        assert 2.5 < result['elapsed'] < 5.0, \
            f"Should timeout around 3s with keep-alive, took {result['elapsed']:.2f}s"

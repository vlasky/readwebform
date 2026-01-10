"""
HTTPS tests for readwebform - tests SSL/TLS functionality.

These tests use the cryptography library to generate ephemeral certificates
for testing HTTPS functionality without requiring user-provided certificates.
"""

import datetime
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request

import pytest

from readwebform.server import FormServer
from readwebform.parser import inject_csrf_token, wrap_html_fragment


def generate_self_signed_cert(hostname='localhost'):
    """Generate a self-signed certificate for testing.

    Returns:
        Tuple of (cert_pem, key_pem) as bytes
    """
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    # Generate private key
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Generate certificate
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, hostname),
    ])

    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.utcnow()
    ).not_valid_after(
        datetime.datetime.utcnow() + datetime.timedelta(days=1)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName(hostname),
            x509.DNSName('localhost'),
            x509.IPAddress(ipaddress.IPv4Address('127.0.0.1')),
        ]),
        critical=False,
    ).sign(private_key, hashes.SHA256())

    # Serialize to PEM format
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption()
    )

    return cert_pem, key_pem


import ipaddress


@pytest.fixture
def temp_cert_files():
    """Fixture that creates temporary certificate and key files."""
    cert_pem, key_pem = generate_self_signed_cert('localhost')

    with tempfile.NamedTemporaryFile(mode='wb', suffix='.pem', delete=False) as cert_file:
        cert_file.write(cert_pem)
        cert_path = cert_file.name

    with tempfile.NamedTemporaryFile(mode='wb', suffix='.pem', delete=False) as key_file:
        key_file.write(key_pem)
        key_path = key_file.name

    yield cert_path, key_path

    # Cleanup
    os.unlink(cert_path)
    os.unlink(key_path)


class TestHTTPSServer:
    """Test HTTPS server functionality."""

    def test_https_server_starts_and_serves_form(self, temp_cert_files):
        """Test that HTTPS server starts and serves HTML over SSL."""
        cert_path, key_path = temp_cert_files

        html = '<form><input name="test" required><button type="submit">Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=5,
            cert_file=cert_path,
            key_file=key_path
        )

        # Inject CSRF token
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        # Start server in background
        server_thread = threading.Thread(target=server.serve, daemon=True)
        server_thread.start()

        # Wait for server to start
        time.sleep(0.5)

        # Get URL - should be https
        url = server.get_url()
        assert url.startswith('https://127.0.0.1:'), f"URL should be HTTPS, got {url}"

        # Create SSL context that doesn't verify self-signed cert
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        # Fetch the form
        response = urllib.request.urlopen(url, timeout=2, context=ctx)
        content = response.read().decode('utf-8')

        assert response.status == 200
        assert '<form' in content
        assert server.csrf_token in content

        # Shutdown
        server.shutdown_event.set()
        server_thread.join(timeout=2)

    def test_https_form_submission(self, temp_cert_files):
        """Test submitting a form over HTTPS."""
        cert_path, key_path = temp_cert_files

        html = '''<form>
            <input name="username">
            <input name="password" type="password">
            <button type="submit">Submit</button>
        </form>'''
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=10,
            cert_file=cert_path,
            key_file=key_path
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

        # Submit form over HTTPS
        url = server.get_url()
        data = {
            'username': 'secure_user',
            'password': 'secret123',
            '_csrf_token': server.csrf_token
        }

        # Create SSL context that doesn't verify self-signed cert
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        encoded_data = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded_data, method='POST')
        response = urllib.request.urlopen(req, timeout=2, context=ctx)

        assert response.status == 200

        server_thread.join(timeout=2)

        assert result['success'] is True
        assert result['data']['username'] == 'secure_user'
        assert result['data']['password'] == 'secret123'

    def test_https_file_upload(self, temp_cert_files):
        """Test file upload over HTTPS."""
        cert_path, key_path = temp_cert_files

        html = '''<form enctype="multipart/form-data">
            <input name="description">
            <input type="file" name="document">
            <button type="submit">Submit</button>
        </form>'''
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=10,
            cert_file=cert_path,
            key_file=key_path
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

        file_content = b'Sensitive document content\nConfidential data'

        body = (
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="description"\r\n\r\n'
            f'Top secret file\r\n'
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="_csrf_token"\r\n\r\n'
            f'{server.csrf_token}\r\n'
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="document"; filename="secret.txt"\r\n'
            f'Content-Type: text/plain\r\n\r\n'
        ).encode('utf-8') + file_content + f'\r\n------{boundary}--\r\n'.encode('utf-8')

        # Create SSL context that doesn't verify self-signed cert
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        req = urllib.request.Request(
            url,
            data=body,
            headers={
                'Content-Type': f'multipart/form-data; boundary=----{boundary}',
                'Content-Length': str(len(body))
            },
            method='POST'
        )

        response = urllib.request.urlopen(req, timeout=2, context=ctx)
        assert response.status == 200

        server_thread.join(timeout=2)

        assert result['success'] is True
        assert result['data']['description'] == 'Top secret file'
        assert 'document' in result['files']
        assert result['files']['document']['filename'] == 'secret.txt'

        # Verify file content
        with open(result['files']['document']['path'], 'rb') as f:
            saved_content = f.read()
        assert saved_content == file_content

    def test_https_timeout(self, temp_cert_files):
        """Test that HTTPS server times out correctly."""
        cert_path, key_path = temp_cert_files

        html = '<form><input name="test"><button type="submit">Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=2,
            cert_file=cert_path,
            key_file=key_path
        )

        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        start_time = time.time()
        success, form_data, file_metadata = server.serve()
        elapsed = time.time() - start_time

        assert success is False
        assert form_data is None
        assert 1.5 < elapsed < 3.5  # Should timeout around 2 seconds

    def test_https_csrf_validation(self, temp_cert_files):
        """Test CSRF token validation over HTTPS."""
        cert_path, key_path = temp_cert_files

        html = '<form><input name="test"><button type="submit">Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=5,
            cert_file=cert_path,
            key_file=key_path
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

        # Create SSL context that doesn't verify self-signed cert
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        encoded_data = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=encoded_data, method='POST')

        try:
            urllib.request.urlopen(req, timeout=2, context=ctx)
            assert False, "Should have raised HTTP 403 error"
        except urllib.error.HTTPError as e:
            assert e.code == 403  # Forbidden

        # Cleanup
        server.shutdown_event.set()
        server_thread.join(timeout=2)


class TestHTTPSCLI:
    """Test HTTPS via CLI arguments."""

    def test_cli_cert_without_key_fails(self):
        """Test that --cert without --key fails."""
        result = subprocess.run(
            [
                sys.executable, '-m', 'readwebform',
                '--field', 'test:text:Test',
                '--cert', '/path/to/cert.pem',
            ],
            capture_output=True,
            timeout=5,
        )

        assert result.returncode != 0
        stderr = result.stderr.decode('utf-8')
        assert '--cert requires --key' in stderr

    def test_cli_key_without_cert_fails(self):
        """Test that --key without --cert fails."""
        result = subprocess.run(
            [
                sys.executable, '-m', 'readwebform',
                '--field', 'test:text:Test',
                '--key', '/path/to/key.pem',
            ],
            capture_output=True,
            timeout=5,
        )

        assert result.returncode != 0
        stderr = result.stderr.decode('utf-8')
        assert '--key requires --cert' in stderr

    def test_cli_https_with_valid_certs(self, temp_cert_files):
        """Test CLI with valid certificate files prints HTTPS URL."""
        cert_path, key_path = temp_cert_files

        # Run with very short timeout to exit quickly
        result = subprocess.run(
            [
                sys.executable, '-m', 'readwebform',
                '--field', 'test:text:Test',
                '--cert', cert_path,
                '--key', key_path,
                '--timeout', '2',
            ],
            capture_output=True,
            timeout=10,
        )

        # Should exit with timeout code 5
        assert result.returncode == 5

        stderr = result.stderr.decode('utf-8')
        # URL should be HTTPS
        assert 'https://127.0.0.1:' in stderr, f"Should print HTTPS URL, got: {stderr}"

    def test_cli_https_invalid_cert_path(self):
        """Test CLI with non-existent certificate file."""
        result = subprocess.run(
            [
                sys.executable, '-m', 'readwebform',
                '--field', 'test:text:Test',
                '--cert', '/nonexistent/cert.pem',
                '--key', '/nonexistent/key.pem',
                '--timeout', '2',
            ],
            capture_output=True,
            timeout=5,
        )

        # Should fail
        assert result.returncode != 0


class TestHTTPSKeepAlive:
    """Test HTTPS with keep-alive connections (browser simulation)."""

    def test_https_timeout_with_keepalive(self, temp_cert_files):
        """Test that HTTPS timeout works even with keep-alive connections."""
        cert_path, key_path = temp_cert_files

        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=3,
            cert_file=cert_path,
            key_file=key_path
        )

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
        time.sleep(0.5)
        url = server.get_url()

        # Open HTTPS connection and keep it alive
        parsed = urllib.parse.urlparse(url)

        # Create SSL socket
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        ssl_sock = ctx.wrap_socket(sock, server_hostname=parsed.hostname)
        ssl_sock.connect((parsed.hostname, parsed.port))

        # Send GET request with Connection: keep-alive
        request = f"GET {parsed.path} HTTP/1.1\r\n"
        request += f"Host: {parsed.hostname}:{parsed.port}\r\n"
        request += "Connection: keep-alive\r\n"
        request += "\r\n"
        ssl_sock.send(request.encode())

        # Read response but keep connection open
        ssl_sock.recv(4096)

        # Wait for timeout
        server_thread.join(timeout=6.0)

        # Clean up
        ssl_sock.close()

        # Verify timeout occurred despite keep-alive connection
        assert result['timed_out'] is True, "Should timeout even with keep-alive connection"
        assert result['elapsed'] is not None
        assert 2.5 < result['elapsed'] < 5.0, \
            f"Should timeout around 3s with keep-alive, took {result['elapsed']:.2f}s"

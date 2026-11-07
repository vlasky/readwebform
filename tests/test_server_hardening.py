"""
Regression tests for request handling edge cases found in code review.

Each test starts a real FormServer on a random port and talks to it with
raw sockets or urllib, the same way a browser would.
"""

import os
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

from readwebform.parser import inject_csrf_token, wrap_html_fragment
from readwebform.server import FormServer


def make_server(html, **kwargs):
    html = wrap_html_fragment(html)
    kwargs.setdefault('timeout', 5)
    server = FormServer(html=html, host='127.0.0.1', port=0, **kwargs)
    server.html = inject_csrf_token(html, server.csrf_token, server.endpoint)
    return server


class Running:
    """Context manager that runs FormServer.serve() in a thread."""

    def __init__(self, server):
        self.server = server
        self.result = None

    def __enter__(self):
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        deadline = time.time() + 2
        while self.server.get_url() == '' and time.time() < deadline:
            time.sleep(0.01)
        return self

    def _run(self):
        self.result = self.server.serve()

    def __exit__(self, *exc):
        self.server.shutdown_event.set()
        self.thread.join(timeout=3)

    def wait(self, timeout=3):
        self.thread.join(timeout=timeout)
        return self.result


def multipart_body(boundary, csrf_token, files):
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="_csrf_token"\r\n\r\n{csrf_token}\r\n']
    for name, filename, data in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
                     f'filename="{filename}"\r\nContent-Type: text/plain\r\n\r\n')
        parts.append(data)
        parts.append('\r\n')
    parts.append(f'--{boundary}--\r\n')
    return ''.join(parts).encode('utf-8')


def raw_request(server, request_bytes):
    port = server.server.server_address[1]
    with socket.create_connection(('127.0.0.1', port), timeout=3) as sock:
        sock.sendall(request_bytes)
        sock.shutdown(socket.SHUT_WR)
        chunks = []
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    return b''.join(chunks)


class TestMultipleFiles:
    def test_two_files_in_one_multiple_field(self):
        server = make_server('<form enctype="multipart/form-data">'
                             '<input type="file" name="docs" multiple><button>Go</button></form>')
        with Running(server) as run:
            body = multipart_body('B1', server.csrf_token,
                                  [('docs', 'a.txt', 'A'), ('docs', 'b.txt', 'B')])
            req = urllib.request.Request(server.get_url(), data=body,
                                         headers={'Content-Type': 'multipart/form-data; boundary=B1'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                assert resp.status == 200
            success, form_data, file_metadata, cancelled = run.wait()

        assert success is True
        metas = file_metadata['docs']
        assert isinstance(metas, list)
        assert [m['filename'] for m in metas] == ['a.txt', 'b.txt']
        for meta, expected in zip(metas, ['A', 'B']):
            with open(meta['path']) as f:
                assert f.read() == expected
            os.unlink(meta['path'])

    def test_same_sanitised_name_does_not_overwrite(self):
        server = make_server('<form enctype="multipart/form-data">'
                             '<input type="file" name="docs" multiple><button>Go</button></form>')
        with Running(server) as run:
            body = multipart_body('B1', server.csrf_token,
                                  [('docs', 'same.txt', 'first'), ('docs', 'same.txt', 'second')])
            req = urllib.request.Request(server.get_url(), data=body,
                                         headers={'Content-Type': 'multipart/form-data; boundary=B1'})
            urllib.request.urlopen(req, timeout=3)
            success, _, file_metadata, _ = run.wait()

        assert success
        paths = [m['path'] for m in file_metadata['docs']]
        assert len(set(paths)) == 2
        contents = sorted(open(p).read() for p in paths)
        assert contents == ['first', 'second']
        for p in paths:
            os.unlink(p)


class TestContentLength:
    def test_negative_content_length_is_rejected(self):
        server = make_server('<form><input name="big"><button>Go</button></form>', max_total_size=8)
        with Running(server):
            payload = f'_csrf_token={server.csrf_token}&big={"x" * 1000}'
            request = (f'POST {server.endpoint} HTTP/1.0\r\n'
                       'Content-Type: application/x-www-form-urlencoded\r\n'
                       'Content-Length: -1\r\n\r\n' + payload).encode()
            response = raw_request(server, request)
            assert response.startswith(b'HTTP/1.0 400')
            assert not server.shutdown_event.is_set()

    def test_missing_content_length_is_rejected(self):
        server = make_server('<form><input name="a"><button>Go</button></form>')
        with Running(server):
            request = (f'POST {server.endpoint} HTTP/1.0\r\n'
                       'Content-Type: application/x-www-form-urlencoded\r\n\r\n'
                       f'_csrf_token={server.csrf_token}&a=1').encode()
            response = raw_request(server, request)
            assert response.startswith(b'HTTP/1.0 411')

    def test_oversized_body_is_rejected_before_parsing(self):
        server = make_server('<form><input name="a"><button>Go</button></form>', max_total_size=8)
        with Running(server):
            data = f'_csrf_token={server.csrf_token}&a={"x" * 100}'.encode()
            req = urllib.request.Request(server.get_url(), data=data)
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(req, timeout=3)
            assert exc.value.code == 413


class TestFilenames:
    def test_utf8_filename_upload_succeeds(self):
        server = make_server('<form enctype="multipart/form-data">'
                             '<input type="file" name="doc"><button>Go</button></form>')
        with Running(server) as run:
            body = multipart_body('B1', server.csrf_token, [('doc', 'café.txt', 'bonjour')])
            req = urllib.request.Request(server.get_url(), data=body,
                                         headers={'Content-Type': 'multipart/form-data; boundary=B1'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                assert resp.status == 200
            success, _, file_metadata, _ = run.wait()

        assert success
        assert file_metadata['doc']['filename'] == 'café.txt'
        assert os.path.basename(file_metadata['doc']['path']) == 'café.txt'
        os.unlink(file_metadata['doc']['path'])

    def test_file_with_trailing_newline_is_byte_exact(self):
        server = make_server('<form enctype="multipart/form-data">'
                             '<input type="file" name="doc"><button>Go</button></form>')
        with Running(server) as run:
            body = multipart_body('B1', server.csrf_token, [('doc', 't.txt', 'hello\n\n')])
            req = urllib.request.Request(server.get_url(), data=body,
                                         headers={'Content-Type': 'multipart/form-data; boundary=B1'})
            urllib.request.urlopen(req, timeout=3)
            success, _, file_metadata, _ = run.wait()

        assert success
        with open(file_metadata['doc']['path'], 'rb') as f:
            assert f.read() == b'hello\n\n'
        os.unlink(file_metadata['doc']['path'])

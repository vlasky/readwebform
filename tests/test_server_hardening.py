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
import urllib.parse
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
        contents = []
        for p in paths:
            with open(p) as f:
                contents.append(f.read())
        contents.sort()
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


def post_fields(server, fields, timeout=3):
    data = urllib.parse.urlencode({'_csrf_token': server.csrf_token, **fields}).encode()
    return urllib.request.urlopen(urllib.request.Request(server.get_url(), data=data), timeout=timeout)


class TestLifecycle:
    def test_listener_is_closed_after_success(self):
        server = make_server('<form><input name="a"><button>Go</button></form>')
        with Running(server) as run:
            port = server.server.server_address[1]
            post_fields(server, {'a': '1'})
            success, _, _, _ = run.wait()
        assert success
        assert server.server.socket.fileno() == -1
        # The port can be bound again immediately
        with socket.socket() as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind(('127.0.0.1', port))

    def test_listener_is_closed_after_timeout(self):
        server = make_server('<form><input name="a"><button>Go</button></form>', timeout=0.5)
        success, _, _, cancelled = server.serve()
        assert not success and not cancelled
        assert server.timed_out
        assert server.server.socket.fileno() == -1

    def test_second_submission_gets_410(self):
        server = make_server('<form><input name="a"><button>Go</button></form>')
        with Running(server) as run:
            post_fields(server, {'a': 'first'})
            try:
                post_fields(server, {'a': 'second'})
                second_status = 200
            except urllib.error.HTTPError as e:
                second_status = e.code
            except (urllib.error.URLError, ConnectionError, OSError):
                second_status = None  # server already gone: also acceptable
            success, form_data, _, _ = run.wait()
        assert success
        assert form_data.fields['a'] == 'first'
        assert second_status in (410, None)

    def test_no_temp_dir_created_without_uploads(self):
        server = make_server('<form><input name="a"><button>Go</button></form>')
        with Running(server) as run:
            post_fields(server, {'a': '1'})
            run.wait()
        assert server.upload_dir is None

    def test_temp_dir_removed_on_timeout(self):
        server = make_server('<form><input name="a"><button>Go</button></form>', timeout=0.3)
        created = server.get_upload_dir()
        assert os.path.isdir(created)
        server.serve()
        assert not os.path.exists(created)

    def test_losing_upload_removes_its_directory(self):
        server = make_server('<form enctype="multipart/form-data">'
                             '<input type="file" name="doc"><button>Go</button></form>', timeout=0.3)
        server.serve()  # times out; no directory yet
        # A handler that had already passed the initial check now tries to save
        from readwebform.server import SessionFinished
        with pytest.raises(SessionFinished):
            server.get_upload_dir()
        assert server.upload_dir is None

    def test_upload_dir_refused_after_external_stop(self):
        from readwebform.server import SessionFinished
        server = make_server('<form><input name="a"><button>Go</button></form>')
        server.shutdown_event.set()
        with pytest.raises(SessionFinished):
            server.get_upload_dir()

    def test_upload_dir_cannot_be_created_after_completion(self):
        from readwebform.server import SessionFinished
        server = make_server('<form><input name="a"><button>Go</button></form>', timeout=0.3)
        server.serve()
        with pytest.raises(SessionFinished):
            server.get_upload_dir()

    def test_complete_is_first_wins_under_contention(self):
        server = make_server('<form><input name="a"><button>Go</button></form>')
        results = []
        barrier = threading.Barrier(8)

        def contender(i):
            barrier.wait()
            results.append(server.complete('success', None, {'n': i}))

        threads = [threading.Thread(target=contender, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 1
        assert server.outcome == 'success'

    def test_stale_timer_callback_is_ignored_after_reset(self):
        server = make_server('<form><input name="a"><button>Go</button></form>', timeout=60)
        server._start_timer()
        stale_generation = server._timer_generation
        server.reset_timeout()  # replaces the timer; a pending old callback must not fire
        server._on_timeout(stale_generation)
        assert server.outcome is None
        assert not server.shutdown_event.is_set()
        server._on_timeout(server._timer_generation)
        assert server.timed_out
        if server.timeout_timer:
            server.timeout_timer.cancel()

    def test_non_ascii_csrf_token_gets_403(self):
        server = make_server('<form><input name="a"><button>Go</button></form>')
        with Running(server):
            data = urllib.parse.urlencode({'_csrf_token': 'caf\u00e9', 'a': '1'}).encode()
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(urllib.request.Request(server.get_url(), data=data), timeout=3)
            assert exc.value.code == 403
            assert not server.shutdown_event.is_set()

    def test_cert_without_key_is_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            FormServer('<form></form>', cert_file=str(tmp_path / 'c.pem'))
        with pytest.raises(ValueError):
            FormServer('<form></form>', key_file=str(tmp_path / 'k.pem'))

    def test_explicit_upload_dir_is_used(self, tmp_path):
        target = tmp_path / 'uploads'
        server = make_server('<form enctype="multipart/form-data">'
                             '<input type="file" name="doc"><button>Go</button></form>',
                             upload_dir=str(target))
        with Running(server) as run:
            body = multipart_body('B1', server.csrf_token, [('doc', 'a.txt', 'A')])
            req = urllib.request.Request(server.get_url(), data=body,
                                         headers={'Content-Type': 'multipart/form-data; boundary=B1'})
            urllib.request.urlopen(req, timeout=3)
            success, _, file_metadata, _ = run.wait()
        assert success
        assert file_metadata['doc']['path'] == str(target / 'a.txt')
        assert target.is_dir()

    def test_external_stop_returns_promptly(self):
        server = make_server('<form><input name="a"><button>Go</button></form>', timeout=30)
        start = time.monotonic()
        with Running(server) as run:
            server.shutdown_event.set()
            result = run.wait()
        assert time.monotonic() - start < 3
        assert result == (False, None, {}, False)
        assert server.outcome == 'stopped'


class TestConcurrentServers:
    def test_two_servers_in_one_process_are_independent(self):
        a = make_server('<form><input name="a"><button>Go</button></form>')
        b = make_server('<form><input name="b"><button>Go</button></form>')
        with Running(a) as run_a, Running(b) as run_b:
            page_a = urllib.request.urlopen(a.get_url(), timeout=3).read().decode()
            page_b = urllib.request.urlopen(b.get_url(), timeout=3).read().decode()
            assert a.csrf_token in page_a and b.csrf_token not in page_a
            assert b.csrf_token in page_b and a.csrf_token not in page_b

            # A's token is rejected by B
            data = urllib.parse.urlencode({'_csrf_token': a.csrf_token, 'b': 'x'}).encode()
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(urllib.request.Request(b.get_url(), data=data), timeout=3)
            assert exc.value.code == 403

            post_fields(b, {'b': 'only b'})
            success_b, data_b, _, _ = run_b.wait()
            assert success_b and data_b.fields == {'b': 'only b'}
            assert not a.shutdown_event.is_set()

            post_fields(a, {'a': 'only a'})
            success_a, data_a, _, _ = run_a.wait()
            assert success_a and data_a.fields == {'a': 'only a'}


class TestIPv6:
    def test_bind_to_ipv6_loopback(self):
        try:
            probe = socket.socket(socket.AF_INET6)
            probe.bind(('::1', 0))
            probe.close()
        except OSError:
            pytest.skip('IPv6 loopback not available')

        server = make_server('<form><input name="a"><button>Go</button></form>')
        server.host = '::1'
        with Running(server) as run:
            assert server.get_url().startswith('http://[::1]:')
            page = urllib.request.urlopen(server.get_url(), timeout=3).read().decode()
            assert server.csrf_token in page
            post_fields(server, {'a': '1'})
            success, _, _, _ = run.wait()
        assert success


class TestIdleConnections:
    def test_idle_http_connection_does_not_block_others(self):
        server = make_server('<form><input name="a"><button>Go</button></form>')
        with Running(server) as run:
            port = server.server.server_address[1]
            idle = socket.create_connection(('127.0.0.1', port))  # connect, send nothing
            time.sleep(0.2)
            start = time.monotonic()
            page = urllib.request.urlopen(server.get_url(), timeout=3).read().decode()
            assert server.csrf_token in page
            assert time.monotonic() - start < 2
            post_fields(server, {'a': '1'})
            success, _, _, _ = run.wait()
            # Shutdown must have closed the idle connection from the server side
            idle.settimeout(2)
            try:
                assert idle.recv(16) == b''  # socket.timeout here means it was NOT closed
            except ConnectionResetError:
                pass
            idle.close()
        assert success

    def test_idle_tls_connection_does_not_block_others(self, tmp_path):
        pytest.importorskip('cryptography')
        import ssl

        from tests.test_https import generate_self_signed_cert
        cert_pem, key_pem = generate_self_signed_cert('localhost')
        cert_path, key_path = str(tmp_path / 'cert.pem'), str(tmp_path / 'key.pem')
        (tmp_path / 'cert.pem').write_bytes(cert_pem)
        (tmp_path / 'key.pem').write_bytes(key_pem)
        server = make_server('<form><input name="a"><button>Go</button></form>',
                             cert_file=cert_path, key_file=key_path)
        with Running(server) as run:
            port = server.server.server_address[1]
            idle = socket.create_connection(('127.0.0.1', port))  # never handshakes
            time.sleep(0.2)
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            start = time.monotonic()
            page = urllib.request.urlopen(server.get_url(), timeout=3, context=ctx).read().decode()
            assert server.csrf_token in page
            assert time.monotonic() - start < 2
            data = urllib.parse.urlencode({'_csrf_token': server.csrf_token, 'a': '1'}).encode()
            urllib.request.urlopen(urllib.request.Request(server.get_url(), data=data),
                                   timeout=3, context=ctx)
            success, _, _, _ = run.wait()
            # The never-handshaked TLS connection is tracked and closed on shutdown
            idle.settimeout(2)
            try:
                assert idle.recv(16) == b''  # socket.timeout here means it was NOT closed
            except ConnectionResetError:
                pass
            idle.close()
        assert success


class TestDisplayURL:
    def test_loopback_host_is_used_verbatim(self):
        server = make_server('<form></form>')
        assert server._display_host() == '127.0.0.1'

    def test_wildcard_host_becomes_reachable_address(self, monkeypatch):
        from readwebform import server as server_mod
        server = make_server('<form></form>')
        server.host = '0.0.0.0'
        monkeypatch.setattr(server_mod, '_primary_address', lambda ipv6=False: '192.0.2.10')
        assert server._build_url(8080) == 'http://192.0.2.10:8080' + server.endpoint

    def test_wildcard_host_falls_back_to_hostname(self, monkeypatch):
        import socket as socket_mod

        from readwebform import server as server_mod
        server = make_server('<form></form>')
        server.host = '::'
        monkeypatch.setattr(server_mod, '_primary_address', lambda ipv6=False: None)
        assert server._display_host() == socket_mod.gethostname()

    def test_primary_address_is_not_wildcard(self):
        from readwebform.server import _primary_address
        address = _primary_address()
        assert address is None or address not in ('0.0.0.0', '::')

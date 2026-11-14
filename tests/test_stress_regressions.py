"""
Regression tests for defects found by running the CLI and server with
hostile inputs (2026-11 stress pass). Each test is the minimal reproduction
of something that used to print a traceback, lose a submission, or leave
files behind.
"""

import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from readwebform.api import read_webform_fields
from readwebform.forms import FieldSpec
from readwebform.multipart import UploadedFile, save_uploaded_file
from readwebform.parser import inject_csrf_token, wrap_html_fragment
from readwebform.server import FormServer

posix_only = pytest.mark.skipif(sys.platform == 'win32', reason='POSIX permission bits')
not_root = pytest.mark.skipif(hasattr(os, 'geteuid') and os.geteuid() == 0,
                              reason='root ignores permission bits')
BASE = ['--no-launch-browser', '--field', 'a:text']


def run_cli(*args, stdin=None, timeout=30):
    return subprocess.run([sys.executable, '-m', 'readwebform', *args],
                          capture_output=True, input=stdin, timeout=timeout,
                          stdin=subprocess.DEVNULL if stdin is None else None)


class LiveCLI:
    """A running readwebform process whose form can be submitted."""

    def __init__(self, *args):
        self.proc = subprocess.Popen([sys.executable, '-m', 'readwebform', *args],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     stdin=subprocess.DEVNULL)
        assert self.proc.stderr is not None
        seen = b''
        while b'/readform_' not in seen:
            line = self.proc.stderr.readline()
            assert line, f'process exited before printing its URL: {seen!r}'
            seen += line
        match = re.search(rb'http://\S+', seen)
        assert match is not None
        self.url = match.group().decode()
        page = urllib.request.urlopen(self.url, timeout=10).read().decode()
        token = re.search(r'name="_csrf_token" value="([0-9a-f]+)"', page)
        assert token is not None
        self.token = token.group(1)

    def submit(self, **fields):
        data = urllib.parse.urlencode(dict(fields, _csrf_token=self.token)).encode()
        return urllib.request.urlopen(urllib.request.Request(self.url, data=data), timeout=10).status

    def finish(self):
        try:
            out, err = self.proc.communicate(timeout=30)
        finally:
            if self.proc.poll() is None:
                self.proc.kill()
                self.proc.communicate()
        return self.proc.returncode, out.decode(), err.decode()


class TestStartupFailuresAreCleanErrors:
    """A bad environment is reported in one line, never as a traceback."""

    def test_port_in_use(self):
        with socket.socket() as busy:
            busy.bind(('127.0.0.1', 0))
            busy.listen()
            result = run_cli(*BASE, '--port', str(busy.getsockname()[1]))
        assert result.returncode == 1
        assert b'Failed to bind' in result.stderr
        assert b'Traceback' not in result.stderr

    def test_unresolvable_host(self):
        result = run_cli(*BASE, '--host', 'nonexistent.invalid')
        assert result.returncode == 1
        assert b'Failed to bind' in result.stderr
        assert b'Traceback' not in result.stderr

    def test_invalid_certificate(self, tmp_path):
        bad = tmp_path / 'bad.pem'
        bad.write_text('not a certificate')
        result = run_cli(*BASE, '--cert', str(bad), '--key', str(bad))
        assert result.returncode == 1
        assert b'SSL configuration failed' in result.stderr
        assert b'Traceback' not in result.stderr

    def test_missing_certificate(self, tmp_path):
        result = run_cli(*BASE, '--cert', str(tmp_path / 'none.pem'), '--key', str(tmp_path / 'none.key'))
        assert result.returncode == 3
        assert b'does not exist' in result.stderr
        assert b'Traceback' not in result.stderr

    def test_stdin_not_utf8(self):
        result = run_cli('--no-launch-browser', stdin=b'\xff\xfe<form></form>')
        assert result.returncode == 3
        assert b'not valid UTF-8' in result.stderr
        assert b'Traceback' not in result.stderr

    @pytest.mark.parametrize('option', ['--envfile', '--url-file'])
    def test_output_file_in_missing_directory(self, option, tmp_path):
        result = run_cli(*BASE, option, str(tmp_path / 'missing' / 'out'))
        assert result.returncode == 3
        assert b'does not exist' in result.stderr
        assert b'http://' not in result.stderr, 'the form must not be shown'

    @pytest.mark.parametrize('option', ['--envfile', '--url-file'])
    def test_output_file_is_a_directory(self, option, tmp_path):
        result = run_cli(*BASE, option, str(tmp_path))
        assert result.returncode == 3
        assert b'is a directory' in result.stderr

    def test_upload_dir_is_a_file(self, tmp_path):
        target = tmp_path / 'file'
        target.write_text('')
        result = run_cli(*BASE, '--upload-dir', str(target))
        assert result.returncode == 3
        assert b'is not a directory' in result.stderr


class TestEmptyAndMalformedArguments:
    """An empty value (usually an unset shell variable) is rejected."""

    @pytest.mark.parametrize('option', ['--host', '--url-host', '--envfile', '--url-file',
                                        '--upload-dir', '--max-file-size', '--max-total-size'])
    def test_empty_value_rejected(self, option):
        result = run_cli(*BASE, option, '')
        assert result.returncode == 8
        assert b'must not be empty' in result.stderr

    @pytest.mark.parametrize('value', ['a b', 'a\nb', 'evil.example/x?', 'user@host', '"><'])
    def test_url_host_must_be_a_host(self, value):
        result = run_cli(*BASE, '--url-host', value)
        assert result.returncode == 8
        assert b'--url-host' in result.stderr

    def test_url_host_brackets_are_not_doubled(self):
        result = run_cli(*BASE, '--timeout', '0.2', '--url-host', '[::1]')
        assert b'http://[::1]:' in result.stderr

    @pytest.mark.parametrize('name', ['_cancel', '_csrf_token'])
    def test_reserved_field_names_rejected(self, name):
        result = run_cli('--no-launch-browser', '--field', f'{name}:text')
        assert result.returncode == 2
        assert b'reserved' in result.stderr
        with pytest.raises(ValueError, match='reserved'):
            FieldSpec.parse(f'{name}:text')
        with pytest.raises(ValueError, match='reserved'):
            read_webform_fields([{'name': name, 'type': 'text'}], timeout=1)


class TestSubmissionIsNotLost:
    @posix_only
    @not_root
    def test_envfile_failure_after_submission_still_prints_result(self, tmp_path):
        target_dir = tmp_path / 'out'
        target_dir.mkdir()
        cli = LiveCLI(*BASE, '--timeout', '30', '--envfile', str(target_dir / 'env'))
        # The directory passes the start-up check, then stops being writable
        target_dir.chmod(0o500)
        try:
            assert cli.submit(a='precious') == 200
            returncode, out, err = cli.finish()
        finally:
            target_dir.chmod(0o700)

        assert returncode == 3
        assert json.loads(out)['fields'] == {'a': 'precious'}
        assert 'Cannot write the envfile' in err
        assert 'Traceback' not in err


def start_server(**kwargs):
    """Start an in-process FormServer; return (server, thread, host, port)."""
    html = wrap_html_fragment('<form enctype="multipart/form-data"><input name="a">'
                              '<input type="file" name="f" multiple><button>Go</button></form>')
    server = FormServer(html=html, host='127.0.0.1', port=0, **kwargs)
    server.html = inject_csrf_token(html, server.csrf_token, server.endpoint)
    outcome = {}

    def run():
        try:
            outcome['result'] = server.serve()
        except BaseException as e:  # reported by the readiness wait below
            outcome['error'] = e

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.get_url():
        assert 'error' not in outcome, f'server failed to start: {outcome["error"]!r}'
        assert time.monotonic() < deadline, 'server did not start'
        time.sleep(0.005)
    host, port = urllib.parse.urlsplit(server.get_url()).netloc.rsplit(':', 1)
    return server, thread, host, int(port), outcome


class TestContentLength:
    @pytest.mark.parametrize('value,expected', [
        ('+10', 400), ('1_0', 400), ('\u0661\u0660', 400), ('-1', 400), ('', 400),
        ('10.0', 400), ('0x10', 400),
        # A number, but Python 3.11+ refuses to convert this many digits.
        # It must be answered as too large, not drop the connection.
        pytest.param('9' * 5000, 413, id='5000-digits'),
    ])
    def test_only_ascii_digits_accepted(self, value, expected):
        """Anything but a plain decimal number is HTTP 400."""
        server, thread, host, port, _ = start_server(timeout=30)
        try:
            with socket.create_connection((host, port), timeout=30) as sock:
                # Headers only: unread body bytes would make the server's
                # close a reset on some platforms and lose the response.
                sock.sendall(f'POST {server.endpoint} HTTP/1.1\r\nHost: x\r\n'
                             f'Content-Length: {value}\r\nConnection: close\r\n\r\n'.encode())
                status = sock.makefile('rb').readline()
            assert f' {expected} '.encode() in status
        finally:
            server.shutdown_event.set()
            thread.join(timeout=30)


class TestUploadSaving:
    def test_many_files_with_one_name_get_sequential_suffixes(self, tmp_path):
        next_suffix = {}
        paths = [save_uploaded_file(UploadedFile(filename='x.txt', content=b'D', content_type='text/plain'),
                                    str(tmp_path), next_suffix)
                 for _ in range(300)]
        names = [os.path.basename(p) for p in paths]
        assert names[:3] == ['x.txt', 'x_1.txt', 'x_2.txt']
        assert len(set(names)) == 300
        # The shared dict means the 300th save starts at the right suffix
        assert next_suffix['x.txt'] == 300

    def test_suffix_hint_still_skips_existing_files(self, tmp_path):
        (tmp_path / 'x_1.txt').write_text('already here')
        next_suffix = {}
        names = [os.path.basename(save_uploaded_file(
            UploadedFile(filename='x.txt', content=b'D', content_type='text/plain'),
            str(tmp_path), next_suffix)) for _ in range(3)]
        assert names == ['x.txt', 'x_2.txt', 'x_3.txt']
        assert (tmp_path / 'x_1.txt').read_text() == 'already here'

    def test_no_files_left_when_timeout_fires_during_save(self, tmp_path, monkeypatch):
        """A submission that loses to the timeout removes what it had saved,
        and serve() does not return until it has."""
        import readwebform.server as server_module

        upload_dir = tmp_path / 'up'
        server, thread, _, _, outcome = start_server(timeout=600, upload_dir=str(upload_dir))
        real_save = server_module.save_uploaded_file
        saved = []
        files_seen_at_timeout = []

        def save_then_time_out(uploaded_file, directory, next_suffix=None):
            path = real_save(uploaded_file, directory, next_suffix)
            saved.append(path)
            if len(saved) == 2:
                # The session times out while this handler is mid-way...
                server._on_timeout(server._timer_generation)
                files_seen_at_timeout.extend(os.listdir(directory))
                # ...and serve() gets every chance to return before the
                # handler has cleaned up. It must wait instead.
                time.sleep(0.5)
            return path

        monkeypatch.setattr(server_module, 'save_uploaded_file', save_then_time_out)

        parts = [b'Content-Disposition: form-data; name="_csrf_token"\r\n\r\n' + server.csrf_token.encode()]
        parts += [b'Content-Disposition: form-data; name="f"; filename="f%d.txt"\r\n\r\nD' % i
                  for i in range(5)]
        body = b''.join(b'--XX\r\n' + p + b'\r\n' for p in parts) + b'--XX--\r\n'
        request = urllib.request.Request(
            server.get_url(), data=body, headers={'Content-Type': 'multipart/form-data; boundary=XX'})
        try:
            status = urllib.request.urlopen(request, timeout=30).status
        except urllib.error.HTTPError as e:
            status = e.code
        except (urllib.error.URLError, ConnectionError):
            status = None  # the server closed the connection as it shut down

        thread.join(timeout=30)
        assert not thread.is_alive()
        # Checked the moment serve() has returned, with no further waiting
        leftover = os.listdir(upload_dir) if upload_dir.exists() else []

        assert len(saved) == 2, 'saving must stop once the session is over'
        assert len(files_seen_at_timeout) == 2, 'two files were on disk when the timeout fired'
        assert leftover == []
        assert status in (410, None)
        assert server.timed_out is True
        assert outcome['result'] == (False, None, {}, False)


class TestReviewFollowUps:
    """Defects in the first round of fixes, found by review."""

    def test_url_file_failure_inside_serve_is_a_file_error(self, tmp_path, monkeypatch, capsys):
        """A URL file that passes the check but cannot be written exits 3,
        and no URL is announced."""
        from readwebform import core
        from readwebform.cli import ReadWebFormArgumentParser

        monkeypatch.setattr(core, 'check_paths', lambda args: None)
        args = ReadWebFormArgumentParser().parse_args(
            [*BASE, '--url-file', str(tmp_path / 'missing' / 'url.txt')])
        assert core.run_readwebform(args) == 3
        err = capsys.readouterr().err
        assert 'Cannot write the URL file' in err
        assert 'http://' not in err
        assert 'Traceback' not in err

    @posix_only
    def test_dangling_symlink_is_checked_at_its_target(self, tmp_path):
        link = tmp_path / 'link'
        link.symlink_to(tmp_path / 'missing' / 'target')
        result = run_cli(*BASE, '--url-file', str(link))
        assert result.returncode == 3
        assert b'does not exist' in result.stderr

    def test_upload_dir_below_a_file_is_rejected(self, tmp_path):
        blocker = tmp_path / 'file'
        blocker.write_text('')
        result = run_cli(*BASE, '--upload-dir', str(blocker / 'uploads'))
        assert result.returncode == 3
        assert b'cannot be created' in result.stderr

    def test_new_nested_upload_dir_is_accepted(self, tmp_path):
        result = run_cli(*BASE, '--timeout', '0.2', '--upload-dir', str(tmp_path / 'a' / 'b'))
        assert result.returncode == 5

    def test_envfile_inside_upload_dir_that_does_not_exist_yet(self, tmp_path):
        uploads = tmp_path / 'uploads'
        result = run_cli(*BASE, '--timeout', '0.2', '--upload-dir', str(uploads),
                         '--envfile', str(uploads / 'vars.env'))
        assert result.returncode == 5, result.stderr

    @posix_only
    def test_whitespace_file_name_is_a_real_name(self, tmp_path):
        form = tmp_path / ' '
        form.write_text('<form><input name="a"><button>Go</button></form>')
        result = run_cli('--no-launch-browser', '--timeout', '0.2', '--htmlfile', str(form))
        assert result.returncode == 5, result.stderr
        result = subprocess.run([sys.executable, '-m', 'readwebform', *BASE, '--timeout', '0.2',
                                 '--envfile', ' '], capture_output=True, cwd=tmp_path,
                                stdin=subprocess.DEVNULL, timeout=30)
        assert result.returncode == 5, result.stderr

    @posix_only
    def test_key_from_a_named_pipe_passes_the_path_check(self, tmp_path):
        import argparse

        from readwebform.core import check_paths

        fifo = tmp_path / 'key.fifo'
        os.mkfifo(fifo)
        check_paths(argparse.Namespace(cert=str(fifo), key=str(fifo)))

    @pytest.mark.parametrize('value', ['example.com', '192.0.2.7', '::1', '[::1]', 'fe80::1%en0',
                                       '[2001:db8::1]', 'bücher.example', 'my_host'])
    def test_url_host_accepted(self, value):
        from readwebform.cli import is_valid_url_host
        assert is_valid_url_host(value)

    @pytest.mark.parametrize('value', ['[]', '[[]]', 'example.com:8080', '[::1]:8080', 'foo]bar',
                                       '[example.com]', 'a\x7fb', ':', 'a b'])
    def test_url_host_rejected(self, value):
        from readwebform.cli import is_valid_url_host
        assert not is_valid_url_host(value)

    @pytest.mark.parametrize('name', [' _cancel ', '   '])
    def test_api_field_names_are_stripped_before_checking(self, name):
        from readwebform.forms import dict_to_fieldspec
        with pytest.raises(ValueError):
            dict_to_fieldspec({'name': name, 'type': 'text'})


class TestInterruptDuringStartup:
    """Ctrl+C in the instant the server is starting.

    Found by interrupting the CLI thousands of times on a busy machine: about
    one run in 150 exited 1 with a traceback, aborted, or hung.
    """

    def _args(self):
        from readwebform.cli import ReadWebFormArgumentParser
        return ReadWebFormArgumentParser().parse_args(BASE)

    def test_interrupt_surfacing_as_another_exception_is_still_an_interrupt(self, monkeypatch, capsys):
        """CPython can turn Ctrl+C inside Thread.start() into
        'RuntimeError: release unlocked lock' raised while handling it."""
        from readwebform import core

        def serve(self, **kwargs):
            try:
                raise KeyboardInterrupt
            except KeyboardInterrupt:
                raise RuntimeError('release unlocked lock')  # noqa: B904

        monkeypatch.setattr(FormServer, 'serve', serve)
        assert core.run_readwebform(self._args()) == 130
        captured = capsys.readouterr()
        assert 'Interrupted by user' in captured.err
        assert 'Traceback' not in captured.err
        assert json.loads(captured.out)['error'] == 'interrupted'

    def test_unrelated_runtime_error_is_still_an_internal_error(self, monkeypatch, capsys):
        from readwebform import core

        def serve(self, **kwargs):
            raise RuntimeError('something else')

        monkeypatch.setattr(FormServer, 'serve', serve)
        assert core.run_readwebform(self._args()) == 1
        assert 'Internal error' in capsys.readouterr().err

    def test_serve_does_not_hang_when_the_interrupt_leaked_the_state_lock(self, monkeypatch):
        import readwebform.server as server_module

        monkeypatch.setattr(server_module, '_LOCK_GRACE', 0.2)
        server = FormServer(html='<form></form>', host='127.0.0.1', port=0, timeout=600)

        def start_timer_interrupted():
            server._lock.acquire()  # as if Ctrl+C landed just after acquiring
            raise KeyboardInterrupt

        monkeypatch.setattr(server, '_start_timer', start_timer_interrupted)
        raised = []

        def run():
            try:
                server.serve()
            except KeyboardInterrupt as e:
                raised.append(e)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(timeout=30)
        assert not thread.is_alive(), 'serve() hung on the leaked lock'
        assert raised, 'the interrupt must still propagate'
        assert server._lock.acquire(timeout=1), 'the lock must be usable again'
        server._lock.release()

    def test_interrupt_inside_thread_start_shuts_the_server_thread_down(self, monkeypatch, capfd):
        """The thread may already be running when Thread.start() is interrupted."""
        original_start = threading.Thread.start
        started = []

        def start(self):
            original_start(self)
            if getattr(self._target, '__name__', '') == '_run_server':
                started.append(self)
                raise KeyboardInterrupt

        monkeypatch.setattr(threading.Thread, 'start', start)
        server = FormServer(html='<form></form>', host='127.0.0.1', port=0, timeout=600)
        with pytest.raises(KeyboardInterrupt):
            server.serve()
        monkeypatch.undo()

        assert started, 'the server thread was started'
        started[0].join(timeout=30)
        assert not started[0].is_alive(), 'the server thread must not be left running'
        err = capfd.readouterr().err
        assert 'Exception in thread' not in err
        assert 'Server error' not in err

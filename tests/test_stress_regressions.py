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


class TestContentLength:
    @pytest.mark.parametrize('value', ['+10', '1_0', '\u0661\u0660', '-1', '', '10.0', '0x10'])
    def test_only_ascii_digits_accepted(self, value):
        html = wrap_html_fragment('<form><input name="a"><button>Go</button></form>')
        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=10)
        server.html = inject_csrf_token(html, server.csrf_token, server.endpoint)

        thread = threading.Thread(target=server.serve, daemon=True)
        thread.start()
        try:
            while not server.get_url():
                time.sleep(0.005)
            host, port = urllib.parse.urlsplit(server.get_url()).netloc.rsplit(':', 1)
            with socket.create_connection((host, int(port)), timeout=10) as sock:
                sock.sendall(f'POST {server.endpoint} HTTP/1.1\r\nHost: x\r\n'
                             f'Content-Length: {value}\r\nConnection: close\r\n\r\n'
                             'abcdefghij'.encode())
                status = sock.makefile('rb').readline()
            assert b' 400 ' in status
        finally:
            server.shutdown_event.set()
            thread.join(timeout=10)


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

    def test_no_files_left_when_timeout_fires_during_save(self, tmp_path):
        """A submission that loses to the timeout removes what it had saved."""
        upload_dir = tmp_path / 'up'
        cli = LiveCLI('--no-launch-browser', '--field', 'f:file', '--timeout', '1',
                      '--upload-dir', str(upload_dir))
        parts = [b'Content-Disposition: form-data; name="_csrf_token"\r\n\r\n' + cli.token.encode()]
        parts += [b'Content-Disposition: form-data; name="f"; filename="x.txt"\r\n\r\nD'] * 30000
        body = b''.join(b'--XX\r\n' + p + b'\r\n' for p in parts) + b'--XX--\r\n'
        request = urllib.request.Request(
            cli.url, data=body, headers={'Content-Type': 'multipart/form-data; boundary=XX'})
        try:
            status = urllib.request.urlopen(request, timeout=60).status
        except urllib.error.HTTPError as e:
            status = e.code
        returncode, out, _ = cli.finish()

        if status == 200:
            # This machine saved 30,000 files inside the timeout: nothing to check
            assert returncode == 0
            assert len(list(upload_dir.iterdir())) == 30000
        else:
            assert status == 410
            assert returncode == 5
            assert json.loads(out)['files'] == {}
            assert not upload_dir.exists() or list(upload_dir.iterdir()) == []

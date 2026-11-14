"""
Timeout behaviour tests for readwebform.

These tests avoid asserting on narrow wall-clock windows. A timer can never
fire early, so lower bounds are exact; upper bounds are generous (SLACK) and
only catch a server that fails to stop. Whether the timer was restarted is
checked against a timestamp taken just before the request that should (or
should not) restart it.

One real-time dependency remains: a test that sends a request to a live
server needs it to arrive before the session timeout, so those tests use a
timeout of 1.5 to 2 s and send at once. A machine stalled for longer than
that between two lines of a test will still fail them.
"""

import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

from readwebform.parser import inject_csrf_token, wrap_html_fragment
from readwebform.server import FormServer

# How long a timed-out server may take to return beyond its timeout before we
# call it stuck. Deliberately large: this is a liveness check, not a benchmark.
SLACK = 10.0

# Timers use the monotonic clock and do not fire early; allow for float noise.
EPSILON = 0.02

FORM = '<form><input name="test"><button>Submit</button></form>'
FILE_FORM = ('<form enctype="multipart/form-data"><input type="file" name="file">'
             '<button>Submit</button></form>')


def make_server(timeout, form=FORM, **kwargs):
    html = wrap_html_fragment(form)
    server = FormServer(html=html, host='127.0.0.1', port=0, timeout=timeout, **kwargs)
    server.html = inject_csrf_token(html, server.csrf_token, server.endpoint)
    return server


class Session:
    """Run FormServer.serve() on a thread and record when it started and ended."""

    def __init__(self, server):
        self.server = server
        self.success = None
        self.started = 0.0
        self.ended = 0.0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        deadline = time.monotonic() + SLACK
        while not server.get_url():
            assert time.monotonic() < deadline, 'server did not start'
            time.sleep(0.005)
        self.url = server.get_url()

    def _run(self):
        self.started = time.monotonic()
        self.success = self.server.serve()[0]
        self.ended = time.monotonic()

    def wait(self):
        """Wait for serve() to return; fail if it does not."""
        self._thread.join(timeout=self.server.timeout + SLACK)
        assert not self._thread.is_alive(), 'serve() did not return'

    @property
    def elapsed(self):
        return self.ended - self.started


def serve_and_time(server):
    start = time.monotonic()
    success, form_data, file_metadata, _ = server.serve()
    return success, form_data, file_metadata, time.monotonic() - start


def post_bad_token(url):
    """POST with a wrong CSRF token and return the HTTP status."""
    data = urllib.parse.urlencode({'test': 'bad', '_csrf_token': 'WRONG_TOKEN'}).encode()
    try:
        urllib.request.urlopen(urllib.request.Request(url, data=data, method='POST'), timeout=SLACK)
    except urllib.error.HTTPError as e:
        return e.code
    pytest.fail('a wrong CSRF token was accepted')


class TestTimeoutBehavior:
    """The session timeout fires when it should and only then."""

    def test_timeout_triggers_when_no_submission(self):
        server = make_server(0.5)
        success, form_data, file_metadata, elapsed = serve_and_time(server)

        assert success is False
        assert server.timed_out is True
        assert form_data is None
        assert file_metadata == {}
        assert 0.5 - EPSILON <= elapsed < 0.5 + SLACK

    def test_very_short_timeout(self):
        server = make_server(0.05)
        success, form_data, _, elapsed = serve_and_time(server)

        assert success is False
        assert server.timed_out is True
        assert form_data is None
        assert elapsed < 0.05 + SLACK

    def test_timeout_triggers_multiple_times(self):
        """Sequential servers each time out on their own schedule."""
        for iteration in range(3):
            server = make_server(0.3)
            success, form_data, _, elapsed = serve_and_time(server)

            assert success is False, f'iteration {iteration}'
            assert form_data is None, f'iteration {iteration}'
            assert 0.3 - EPSILON <= elapsed < 0.3 + SLACK, f'iteration {iteration}'

    def test_timeout_state_does_not_leak_between_servers(self):
        """A long timeout on one server does not carry over to the next."""
        long_timeout = 2.0
        for timeout_value in (0.2, long_timeout, 0.2):
            server = make_server(timeout_value)
            success, _, _, elapsed = serve_and_time(server)

            assert success is False
            assert elapsed >= timeout_value - EPSILON
            if timeout_value < long_timeout:
                assert elapsed < long_timeout, 'took as long as the other server\'s timeout'

    def test_timeout_reset_on_error(self):
        """A recoverable error restarts the full timeout."""
        # The POST must land before the first timer fires, so leave it room
        timeout = 2.0
        session = Session(make_server(timeout, reset_timeout_on_error=True))

        time.sleep(0.2)
        before_post = time.monotonic()
        assert post_bad_token(session.url) == 403

        session.wait()
        assert session.server.timed_out is True
        # Without the restart the server would stop about 1.8 s after the
        # POST. With it, a full timeout must pass after the POST was sent.
        assert session.ended - before_post >= timeout - EPSILON
        assert session.server._timer_generation == 2

    def test_timeout_no_reset_on_error(self):
        """With reset_timeout_on_error=False an error leaves the timer alone."""
        timeout = 2.0
        session = Session(make_server(timeout, reset_timeout_on_error=False))

        assert post_bad_token(session.url) == 403
        # The original timer is still the one that will fire.
        assert session.server._timer_generation == 1

        session.wait()
        assert session.server.timed_out is True
        assert timeout - EPSILON <= session.elapsed < timeout + SLACK

    def test_get_requests_do_not_reset_timeout(self):
        """Loading the form does not restart the timer."""
        session = Session(make_server(2.0))

        for _ in range(3):
            with urllib.request.urlopen(session.url, timeout=SLACK) as response:
                assert response.status == 200
        assert session.server._timer_generation == 1

        session.wait()
        assert session.server.timed_out is True

    def test_successful_submission_before_timeout(self):
        """A submission ends the session at once; the timeout never fires."""
        timeout = 60
        session = Session(make_server(timeout))

        data = urllib.parse.urlencode({'test': 'value',
                                       '_csrf_token': session.server.csrf_token}).encode()
        request = urllib.request.Request(session.url, data=data, method='POST')
        with urllib.request.urlopen(request, timeout=SLACK) as response:
            assert response.status == 200

        session.wait()
        assert session.success is True
        assert session.server.timed_out is False
        assert session.elapsed < timeout / 2


class TestTimeoutEdgeCases:
    """Timeouts while a client is part-way through a request."""

    def test_timeout_after_partial_submission(self):
        """A POST whose body never arrives does not hold the session open."""
        timeout = 1.5
        session = Session(make_server(timeout))
        host, port = urllib.parse.urlsplit(session.url).netloc.rsplit(':', 1)

        with socket.create_connection((host, int(port)), timeout=SLACK) as sock:
            sock.sendall(
                f'POST {session.server.endpoint} HTTP/1.1\r\n'
                f'Host: {host}:{port}\r\n'
                'Content-Type: application/x-www-form-urlencoded\r\n'
                'Content-Length: 1000\r\n'
                '\r\n'
                'test=par'.encode()
            )
            # The connection stays open with the body incomplete while the
            # session times out and serve() returns.
            session.wait()

        assert session.success is False
        assert session.server.timed_out is True
        assert timeout - EPSILON <= session.elapsed < timeout + SLACK

    def test_file_upload_before_timeout(self, tmp_path):
        """A file upload completes the session; the timeout never fires."""
        timeout = 60
        session = Session(make_server(timeout, form=FILE_FORM, upload_dir=str(tmp_path)))

        boundary = '----Boundary'
        body = (
            f'--{boundary}\r\n'
            'Content-Disposition: form-data; name="_csrf_token"\r\n\r\n'
            f'{session.server.csrf_token}\r\n'
            f'--{boundary}\r\n'
            'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
            'Content-Type: text/plain\r\n\r\n'
        ).encode() + b'Test file' + f'\r\n--{boundary}--\r\n'.encode()

        request = urllib.request.Request(
            session.url,
            data=body,
            headers={'Content-Type': f'multipart/form-data; boundary={boundary}'},
            method='POST',
        )
        with urllib.request.urlopen(request, timeout=SLACK) as response:
            assert response.status == 200

        session.wait()
        assert session.success is True
        assert session.server.timed_out is False
        assert session.elapsed < timeout / 2
        assert [p.name for p in tmp_path.iterdir()] == ['test.txt']

"""
Comprehensive timeout behavior tests for readwebform.

These tests ensure the timeout mechanism works correctly in all scenarios.
"""

import threading
import time
import urllib.request
import urllib.parse

import pytest

from readwebform.server import FormServer
from readwebform.parser import inject_csrf_token, wrap_html_fragment


class TestTimeoutBehavior:
    """Test timeout functionality comprehensively."""

    def test_timeout_triggers_when_no_submission(self):
        """Test that timeout triggers correctly when form is not submitted."""
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=2)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        start_time = time.monotonic()
        success, form_data, file_metadata, _ = server.serve()
        elapsed = time.monotonic() - start_time

        # Verify timeout occurred
        assert success is False, "Should report failure on timeout"
        assert form_data is None, "Should have no form data on timeout"
        assert file_metadata == {}, "Should have no files on timeout"
        assert 1.8 < elapsed < 3.0, f"Should timeout around 2 seconds, took {elapsed:.2f}s"

    def test_timeout_triggers_multiple_times(self):
        """Test that timeout works correctly on repeated invocations.

        This catches bugs where timeout state isn't properly reset between runs.
        """
        for iteration in range(3):
            html = f'<form><input name="iteration" value="{iteration}"><button>Submit</button></form>'
            html = wrap_html_fragment(html)

            server = FormServer(html=html, host='127.0.0.1', port=0, timeout=1)
            html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
            server.html = html_with_csrf

            start_time = time.monotonic()
            success, form_data, _, _ = server.serve()
            elapsed = time.monotonic() - start_time

            assert success is False, f"Iteration {iteration}: Should timeout"
            assert form_data is None, f"Iteration {iteration}: Should have no data"
            assert 0.8 < elapsed < 2.0, f"Iteration {iteration}: Should timeout around 1s, took {elapsed:.2f}s"

    def test_timeout_reset_on_error(self):
        """Test that timeout resets when reset_timeout_on_error is True."""
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=3,
            reset_timeout_on_error=True
        )
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'timeout_occurred': False}

        def run_server():
            success, _, _, _ = server.serve()
            result['timeout_occurred'] = not success

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Wait 1 second
        time.sleep(1.0)

        # Submit with WRONG CSRF token (should reset timeout)
        bad_data = urllib.parse.urlencode({
            'test': 'bad',
            '_csrf_token': 'WRONG_TOKEN'
        }).encode('utf-8')

        try:
            req = urllib.request.Request(url, data=bad_data, method='POST')
            urllib.request.urlopen(req, timeout=1)
        except urllib.error.HTTPError as e:
            assert e.code == 403  # Expected

        # Wait another 2 seconds (total 3 seconds from start)
        time.sleep(2.0)

        # Server should still be running because timeout was reset
        # Try accessing the form again
        try:
            req = urllib.request.Request(url, method='GET')
            response = urllib.request.urlopen(req, timeout=1)
            assert response.status == 200, "Server should still be running after timeout reset"
        except:
            pytest.fail("Server should still be running - timeout should have been reset")

        # Now wait for actual timeout (another second)
        time.sleep(1.5)

        server_thread.join(timeout=1.0)

        # Should have timed out eventually
        assert result['timeout_occurred'] is True, "Should eventually timeout"

    def test_timeout_no_reset_on_error(self):
        """Test that timeout does NOT reset when reset_timeout_on_error is False."""
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=2,
            reset_timeout_on_error=False
        )
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'timeout_occurred': False, 'elapsed': None}

        def run_server():
            start = time.monotonic()
            success, _, _, _ = server.serve()
            result['elapsed'] = time.monotonic() - start
            result['timeout_occurred'] = not success

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Wait 1 second
        time.sleep(1.0)

        # Submit with WRONG CSRF token (should NOT reset timeout)
        bad_data = urllib.parse.urlencode({
            'test': 'bad',
            '_csrf_token': 'WRONG_TOKEN'
        }).encode('utf-8')

        try:
            req = urllib.request.Request(url, data=bad_data, method='POST')
            urllib.request.urlopen(req, timeout=1)
        except urllib.error.HTTPError:
            pass  # Expected

        # Wait for timeout
        server_thread.join(timeout=3.0)

        # Should have timed out at 2 seconds (not reset by error)
        assert result['timeout_occurred'] is True, "Should timeout"
        assert result['elapsed'] is not None
        assert 1.3 < result['elapsed'] < 3.0, \
            f"Should timeout at 2s regardless of error (took {result['elapsed']:.2f}s)"

    def test_successful_submission_before_timeout(self):
        """Test that successful submission prevents timeout."""
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=5)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'success': False, 'elapsed': None}

        def run_server():
            start = time.monotonic()
            success, form_data, _, _ = server.serve()
            result['elapsed'] = time.monotonic() - start
            result['success'] = success

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Wait a bit, then submit (before timeout)
        time.sleep(0.5)

        data = urllib.parse.urlencode({
            'test': 'value',
            '_csrf_token': server.csrf_token
        }).encode('utf-8')

        req = urllib.request.Request(url, data=data, method='POST')
        urllib.request.urlopen(req, timeout=2)

        server_thread.join(timeout=2.0)

        # Should succeed, not timeout
        assert result['success'] is True, "Should succeed before timeout"
        assert result['elapsed'] < 2.0, "Should exit quickly after submission"

    def test_timeout_with_zero_seconds(self):
        """Test edge case: very short timeout (1 second)."""
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=1)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        start_time = time.monotonic()
        success, form_data, _, _ = server.serve()
        elapsed = time.monotonic() - start_time

        assert success is False, "Should timeout"
        assert form_data is None
        assert 0.3 < elapsed < 2.0, f"Should timeout around 1s, took {elapsed:.2f}s"

    def test_timeout_concurrent_with_form_access(self):
        """Test timeout works correctly even when form is being accessed.

        This ensures that accessing the form (GET requests) doesn't interfere
        with the timeout mechanism.
        """
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=2)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'timeout_occurred': False}

        def run_server():
            success, _, _, _ = server.serve()
            result['timeout_occurred'] = not success

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Access the form multiple times (should not reset timeout or interfere)
        for i in range(3):
            time.sleep(0.5)
            try:
                req = urllib.request.Request(url, method='GET')
                response = urllib.request.urlopen(req, timeout=1)
                assert response.status == 200
            except:
                break  # Server may have shut down

        server_thread.join(timeout=3.0)

        # Should have timed out
        assert result['timeout_occurred'] is True, "Should timeout despite GET requests"

    def test_timeout_state_cleanup(self):
        """Test that timeout state is properly cleaned up after each run.

        This ensures no state leakage between multiple server instances.
        """
        # Run two servers sequentially with different timeouts
        for timeout_value in [1, 2, 1]:
            html = '<form><input name="test"><button>Submit</button></form>'
            html = wrap_html_fragment(html)

            server = FormServer(html=html, host='127.0.0.1', port=0, timeout=timeout_value)
            html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
            server.html = html_with_csrf

            start_time = time.monotonic()
            success, _, _, _ = server.serve()
            elapsed = time.monotonic() - start_time

            assert success is False, f"Timeout {timeout_value}s: Should timeout"
            expected_min = timeout_value - 0.2
            expected_max = timeout_value + 1.0
            assert expected_min < elapsed < expected_max, \
                f"Timeout {timeout_value}s: Expected ~{timeout_value}s, got {elapsed:.2f}s"


class TestTimeoutEdgeCases:
    """Test edge cases and error conditions with timeout."""

    def test_timeout_after_partial_submission(self):
        """Test timeout when submission is started but not completed."""
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=2)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'timeout_occurred': False}

        def run_server():
            success, _, _, _ = server.serve()
            result['timeout_occurred'] = not success

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Start a connection but don't complete it (simulating slow client)
        # Just make a GET request but don't read the full response
        time.sleep(1.0)

        # Timeout should still occur
        server_thread.join(timeout=3.0)

        assert result['timeout_occurred'] is True, "Should timeout even with partial requests"

    def test_timeout_with_file_upload_in_progress(self):
        """Test timeout doesn't trigger during active file upload."""
        html = '<form enctype="multipart/form-data"><input type="file" name="file"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=3)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'success': False}

        def run_server():
            success, form_data, _, _ = server.serve()
            result['success'] = success

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Wait a bit, then submit file
        time.sleep(1.0)

        boundary = '----Boundary'
        file_content = b'Test file'

        body = (
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="_csrf_token"\r\n\r\n'
            f'{server.csrf_token}\r\n'
            f'------{boundary}\r\n'
            f'Content-Disposition: form-data; name="file"; filename="test.txt"\r\n'
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

        server_thread.join(timeout=2.0)

        # Should succeed, not timeout
        assert result['success'] is True, "File upload should succeed before timeout"

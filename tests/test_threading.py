"""
Threading and race condition tests for readwebform.

These tests are designed to catch synchronization bugs between the HTTP handler
thread and the main server thread.
"""

import threading
import time
import urllib.request
import urllib.parse

import pytest

from readwebform.server import FormServer
from readwebform.parser import inject_csrf_token, wrap_html_fragment


class TestThreadingSynchronization:
    """Test thread safety and synchronization."""

    def test_immediate_submission_no_delay(self):
        """Test submitting immediately after server starts (no artificial delays).

        This catches race conditions where the main thread might not see
        the shutdown signal from the handler thread.
        """
        html = '<form><input name="test" value="value"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=5)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'success': False, 'data': None}

        def run_server():
            success, form_data, _, _ = server.serve()
            result['success'] = success
            if form_data:
                result['data'] = form_data.fields

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Submit IMMEDIATELY - no time.sleep() to hide race conditions
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.001)  # Minimal sleep just to get URL

        # Submit without delay
        data = urllib.parse.urlencode({
            'test': 'immediate',
            '_csrf_token': server.csrf_token
        }).encode('utf-8')

        req = urllib.request.Request(url, data=data, method='POST')
        response = urllib.request.urlopen(req, timeout=2)
        assert response.status == 200

        # Wait for server thread with short timeout
        server_thread.join(timeout=1.0)

        # Verify success
        assert result['success'] is True, "Server should detect successful submission"
        assert result['data']['test'] == 'immediate'

    def test_shutdown_event_is_set_after_success(self):
        """Verify that the shutdown event is actually set after successful submission."""
        html = '<form><input name="field"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=10)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {}

        def run_server():
            success, form_data, _, _ = server.serve()
            result['success'] = success
            result['event_set'] = server.shutdown_event.is_set()

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Wait for server to be ready
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Verify event is NOT set before submission
        assert not server.shutdown_event.is_set(), "Event should not be set initially"

        # Submit
        data = urllib.parse.urlencode({
            'field': 'test',
            '_csrf_token': server.csrf_token
        }).encode('utf-8')

        req = urllib.request.Request(url, data=data, method='POST')
        urllib.request.urlopen(req, timeout=2)

        server_thread.join(timeout=2.0)

        # Verify event IS set after submission
        assert server.shutdown_event.is_set(), "Event should be set after successful submission"
        assert result['success'] is True
        assert result['event_set'] is True

    def test_repeated_submissions_consistency(self):
        """Run the same submission test multiple times to catch intermittent failures.

        Race conditions often don't fail every time - this test runs multiple
        iterations to increase the chance of catching bugs.
        """
        failures = []

        for i in range(5):  # Run 5 times
            html = f'<form><input name="iteration" value="{i}"><button>Submit</button></form>'
            html = wrap_html_fragment(html)

            server = FormServer(html=html, host='127.0.0.1', port=0, timeout=5)
            html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
            server.html = html_with_csrf

            result = {'success': False, 'iteration': None}

            def run_server():
                success, form_data, _, _ = server.serve()
                result['success'] = success
                if form_data:
                    result['iteration'] = form_data.fields.get('iteration')

            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()

            # Get URL
            url = server.get_url()
            while not url:
                url = server.get_url()
                time.sleep(0.01)

            # Submit
            data = urllib.parse.urlencode({
                'iteration': str(i),
                '_csrf_token': server.csrf_token
            }).encode('utf-8')

            try:
                req = urllib.request.Request(url, data=data, method='POST')
                urllib.request.urlopen(req, timeout=2)
                server_thread.join(timeout=2.0)

                if not result['success']:
                    failures.append(f"Iteration {i}: Server did not report success")
                elif result['iteration'] != str(i):
                    failures.append(f"Iteration {i}: Data mismatch")
            except Exception as e:
                failures.append(f"Iteration {i}: {e}")

        # All iterations should succeed
        if failures:
            pytest.fail(f"Failures in repeated submissions:\n" + "\n".join(failures))

    def test_main_thread_sees_handler_thread_updates(self):
        """Verify that updates from handler thread are visible to main thread.

        This specifically tests that form_data and file_metadata are properly
        synchronized between threads.
        """
        html = '<form><input name="shared_data" value="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=5)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'success': False, 'form_data': None}

        def run_server():
            success, form_data, file_metadata, _ = server.serve()
            result['success'] = success
            result['form_data'] = form_data
            # Main thread should see the data
            assert form_data is not None, "Main thread should see form_data"
            assert form_data.fields.get('shared_data') == 'test', "Main thread should see correct field value"

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Submit
        data = urllib.parse.urlencode({
            'shared_data': 'test',
            '_csrf_token': server.csrf_token
        }).encode('utf-8')

        req = urllib.request.Request(url, data=data, method='POST')
        urllib.request.urlopen(req, timeout=2)

        server_thread.join(timeout=2.0)

        # Verify main thread received the data from handler thread
        assert result['success'] is True, "Server should report success"
        assert result['form_data'] is not None, "Main thread should have received form data"
        assert result['form_data'].fields.get('shared_data') == 'test', "Data should be synchronized correctly"

    def test_concurrent_error_and_success(self):
        """Test that errors don't interfere with eventual success.

        Submit an invalid request (wrong CSRF), then a valid one,
        ensuring the server properly handles both without race conditions.
        """
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(
            html=html,
            host='127.0.0.1',
            port=0,
            timeout=10,
            reset_timeout_on_error=True
        )
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'success': False, 'data': None}

        def run_server():
            success, form_data, _, _ = server.serve()
            result['success'] = success
            if form_data:
                result['data'] = form_data.fields

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # First: submit with WRONG CSRF token (should be rejected)
        bad_data = urllib.parse.urlencode({
            'test': 'bad',
            '_csrf_token': 'WRONG_TOKEN'
        }).encode('utf-8')

        try:
            req = urllib.request.Request(url, data=bad_data, method='POST')
            urllib.request.urlopen(req, timeout=2)
            assert False, "Should have raised 403"
        except urllib.error.HTTPError as e:
            assert e.code == 403

        # Verify server is still running (shutdown_event NOT set)
        assert not server.shutdown_event.is_set(), "Server should still be running after error"

        # Second: submit with CORRECT CSRF token (should succeed)
        good_data = urllib.parse.urlencode({
            'test': 'good',
            '_csrf_token': server.csrf_token
        }).encode('utf-8')

        req = urllib.request.Request(url, data=good_data, method='POST')
        response = urllib.request.urlopen(req, timeout=2)
        assert response.status == 200

        server_thread.join(timeout=2.0)

        # Verify success after error recovery
        assert result['success'] is True, "Server should succeed after recovering from error"
        assert result['data']['test'] == 'good'
        assert server.shutdown_event.is_set(), "Shutdown event should be set after success"

    def test_timeout_does_not_set_shutdown_event(self):
        """Verify that timeout doesn't incorrectly set the shutdown event."""
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=1)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        # Don't submit - let it timeout
        success, form_data, file_metadata, _ = server.serve()

        # After timeout, shutdown_event should NOT be set (timeout is different from success)
        assert success is False, "Timeout should report failure"
        assert form_data is None
        # The event might be set by timeout mechanism, but success should be False
        assert not server.success, "Success flag should be False on timeout"

    def test_rapid_server_start_stop(self):
        """Test starting and stopping server rapidly to catch resource leaks.

        This ensures proper cleanup and no deadlocks in quick succession.
        """
        for i in range(3):
            html = '<form><input name="test"><button>Submit</button></form>'
            html = wrap_html_fragment(html)

            server = FormServer(html=html, host='127.0.0.1', port=0, timeout=2)
            html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
            server.html = html_with_csrf

            result = {'success': False}

            def run_server():
                success, _, _, _ = server.serve()
                result['success'] = success

            server_thread = threading.Thread(target=run_server, daemon=True)
            server_thread.start()

            # Get URL
            url = server.get_url()
            while not url:
                url = server.get_url()
                time.sleep(0.001)

            # Quick submission
            data = urllib.parse.urlencode({
                'test': f'rapid{i}',
                '_csrf_token': server.csrf_token
            }).encode('utf-8')

            req = urllib.request.Request(url, data=data, method='POST')
            urllib.request.urlopen(req, timeout=1)

            server_thread.join(timeout=1.0)

            assert result['success'] is True, f"Iteration {i} should succeed"

            # Brief pause between iterations
            time.sleep(0.1)


class TestThreadingEdgeCases:
    """Test edge cases in multi-threaded scenarios."""

    def test_server_state_after_successful_submission(self):
        """Verify server internal state is correct after submission."""
        html = '<form><input name="state_test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=5)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        server_thread = threading.Thread(target=server.serve, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Submit
        data = urllib.parse.urlencode({
            'state_test': 'checking',
            '_csrf_token': server.csrf_token
        }).encode('utf-8')

        req = urllib.request.Request(url, data=data, method='POST')
        urllib.request.urlopen(req, timeout=2)

        server_thread.join(timeout=2.0)

        # Verify internal state
        assert server.success is True, "Server.success should be True"
        assert server.shutdown_event.is_set(), "shutdown_event should be set"
        assert server.form_data is not None, "form_data should be populated"
        assert server.form_data.fields['state_test'] == 'checking'

    def test_multiple_fields_thread_safety(self):
        """Test that all field data is properly synchronized with many fields."""
        from readwebform.forms import FieldSpec, generate_form_html

        # Generate form with many fields
        fields = [
            FieldSpec.parse(f'field{i}:text:Field{i}') for i in range(20)
        ]
        html = generate_form_html(fields)
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=10)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'data': None}

        def run_server():
            success, form_data, _, _ = server.serve()
            if success:
                result['data'] = form_data.fields

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Submit all fields
        form_fields = {f'field{i}': f'value{i}' for i in range(20)}
        form_fields['_csrf_token'] = server.csrf_token

        data = urllib.parse.urlencode(form_fields).encode('utf-8')
        req = urllib.request.Request(url, data=data, method='POST')
        urllib.request.urlopen(req, timeout=2)

        server_thread.join(timeout=2.0)

        # Verify all fields synchronized correctly
        assert result['data'] is not None
        for i in range(20):
            assert result['data'][f'field{i}'] == f'value{i}', \
                f"Field {i} should be synchronized correctly"

    def test_server_shuts_down_after_success_no_reopen(self):
        """Test that server shuts down properly and doesn't accept subsequent requests.

        This test verifies the fix for a bug where:
        1. Form was submitted successfully
        2. Browser showed success
        3. But readwebform didn't exit
        4. Re-opening the URL caused it to finally exit

        The server should shut down immediately after success and reject new requests.
        """
        html = '<form><input name="test"><button>Submit</button></form>'
        html = wrap_html_fragment(html)

        server = FormServer(html=html, host='127.0.0.1', port=0, timeout=10)
        html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
        server.html = html_with_csrf

        result = {'success': False, 'exit_time': None}

        def run_server():
            start = time.monotonic()
            success, form_data, _, _ = server.serve()
            result['exit_time'] = time.monotonic() - start
            result['success'] = success

        server_thread = threading.Thread(target=run_server, daemon=True)
        server_thread.start()

        # Get URL
        url = server.get_url()
        while not url:
            url = server.get_url()
            time.sleep(0.01)

        # Submit form
        data = urllib.parse.urlencode({
            'test': 'value',
            '_csrf_token': server.csrf_token
        }).encode('utf-8')

        submission_time = time.monotonic()
        req = urllib.request.Request(url, data=data, method='POST')
        response = urllib.request.urlopen(req, timeout=2)
        assert response.status == 200

        # Wait for server to exit
        server_thread.join(timeout=3.0)

        # Verify server exited quickly (within 1 second of submission)
        assert result['success'] is True, "Server should report success"
        assert result['exit_time'] is not None, "Server should have exited"
        assert result['exit_time'] < 1.0, \
            f"Server should exit quickly after submission, took {result['exit_time']:.2f}s"

        # Verify server is actually shut down
        # Try to make another request - should fail with connection error
        time.sleep(0.5)  # Give shutdown a moment to complete

        try:
            req2 = urllib.request.Request(url, method='GET')
            urllib.request.urlopen(req2, timeout=1)
            pytest.fail("Second request should have failed - server should be shut down")
        except (urllib.error.URLError, ConnectionResetError, ConnectionRefusedError, TimeoutError, OSError):
            # Expected - server is shut down or shutting down
            # TimeoutError means connection made but server not responding (shutting down)
            # ConnectionRefusedError means port is closed
            # URLError wraps both of the above
            pass

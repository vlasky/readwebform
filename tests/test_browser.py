"""
Tests for browser launching: a launched browser must never write to our stdout.
"""

import subprocess
import sys
import textwrap


def test_custom_launcher_output_does_not_reach_stdout():
    script = textwrap.dedent('''
        import time
        from readwebform.browser import launch_browser
        assert launch_browser('http://example.invalid/', '/bin/echo') is True
        time.sleep(0.3)
        print('{"marker": true}')
    ''')
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '{"marker": true}\n'


def test_default_launcher_output_does_not_reach_stdout(tmp_path):
    # Register a fake "default" browser that prints to stdout, then check our
    # stdout stays clean.
    script = textwrap.dedent('''
        import time, webbrowser
        from readwebform.browser import launch_browser
        webbrowser.register('fake', None, webbrowser.BackgroundBrowser('/bin/echo'), preferred=True)
        launch_browser('http://example.invalid/')
        time.sleep(0.3)
        print('{"marker": true}')
    ''')
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '{"marker": true}\n'

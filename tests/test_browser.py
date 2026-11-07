"""
Tests for browser launching: a launched browser must never write to our stdout.
"""

import os
import subprocess
import sys
import textwrap

import pytest

posix_only = pytest.mark.skipif(sys.platform == 'win32', reason='uses /bin/echo')
SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src')


@posix_only
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


@posix_only
def test_default_launcher_output_does_not_reach_stdout():
    # $BROWSER makes /bin/echo the "default browser" inside the launcher child.
    # echo prints the URL: that must not reach our stdout, and the launch must
    # report success (echo exits 0).
    script = textwrap.dedent('''
        import time
        from readwebform.browser import launch_browser
        assert launch_browser('http://example.invalid/') is True
        time.sleep(0.3)
        print('{"marker": true}')
    ''')
    env = {'BROWSER': '/bin/echo', 'PATH': '/usr/bin:/bin', 'PYTHONPATH': SRC}
    result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True,
                            timeout=15, env=env)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '{"marker": true}\n'


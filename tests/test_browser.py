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



class TestGuiAvailable:
    def _clean(self, monkeypatch):
        for var in ('CI', 'READWEBFORM_NO_BROWSER', 'SSH_CONNECTION', 'SSH_TTY', 'DISPLAY', 'WAYLAND_DISPLAY'):
            monkeypatch.delenv(var, raising=False)

    def test_ci_disables(self, monkeypatch):
        from readwebform.browser import gui_available
        self._clean(monkeypatch)
        monkeypatch.setenv('CI', 'true')
        assert gui_available() is False

    def test_ssh_disables(self, monkeypatch):
        from readwebform.browser import gui_available
        self._clean(monkeypatch)
        monkeypatch.setenv('SSH_CONNECTION', '1.2.3.4 1 5.6.7.8 22')
        assert gui_available() is False

    def test_opt_out_variable(self, monkeypatch):
        from readwebform.browser import gui_available
        self._clean(monkeypatch)
        monkeypatch.setenv('READWEBFORM_NO_BROWSER', '1')
        assert gui_available() is False

    def test_linux_needs_display(self, monkeypatch):
        from readwebform import browser
        self._clean(monkeypatch)
        monkeypatch.setattr(browser.sys, 'platform', 'linux')
        assert browser.gui_available() is False
        monkeypatch.setenv('DISPLAY', ':0')
        assert browser.gui_available() is True

    def test_desktop_platforms(self, monkeypatch):
        from readwebform import browser
        self._clean(monkeypatch)
        for platform in ('darwin', 'win32'):
            monkeypatch.setattr(browser.sys, 'platform', platform)
            assert browser.gui_available() is True


@posix_only
def test_custom_launcher_exit_status_is_reported(tmp_path):
    from readwebform.browser import launch_browser
    ok = tmp_path / 'ok.sh'
    ok.write_text('#!/bin/sh\nexit 0\n')
    bad = tmp_path / 'bad.sh'
    bad.write_text('#!/bin/sh\nexit 3\n')
    for script in (ok, bad):
        script.chmod(0o755)
    assert launch_browser('http://example.invalid/', str(ok)) is True
    assert launch_browser('http://example.invalid/', str(bad)) is False
    assert launch_browser('http://example.invalid/', str(tmp_path / 'missing')) is False

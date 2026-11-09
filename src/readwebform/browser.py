"""
Cross-platform browser launching for readwebform.

The CLI's contract is that stdout carries only the JSON result, so anything
a browser process prints must never reach our stdout. Browsers are therefore
always started from a child process whose standard streams point at the null
device: a custom browser directly, the system default browser through a
short-lived Python child that calls ``webbrowser.open``. Nothing in this
process's file descriptors is touched.
"""

import os
import subprocess
import sys
import webbrowser
from typing import Optional

_DEFAULT_LAUNCHER = (
    'import sys, webbrowser\n'
    'sys.exit(0 if webbrowser.open(sys.argv[1]) else 1)\n'
)

# How long to wait for a launcher child to report an immediate failure (no
# browser found, bad executable). Background browsers hand off within this
# time; a child still running afterwards is treated as success so the caller
# is not held up. Reporting is therefore best effort. Terminal browsers
# (lynx, w3m) need a tty and are not supported by --launch-browser: the URL
# is always printed to stderr for manual use.
_LAUNCH_WAIT = 1.0


def gui_available() -> bool:
    """
    Guess whether a browser can be opened on a desktop the user is looking at.

    False in CI (``CI`` set), over SSH (``SSH_CONNECTION``/``SSH_TTY`` set), when
    ``READWEBFORM_NO_BROWSER`` is set, and on Unix without ``DISPLAY`` or
    ``WAYLAND_DISPLAY``. True on macOS and Windows otherwise.
    """
    env = os.environ
    if env.get('CI') or env.get('READWEBFORM_NO_BROWSER'):
        return False
    if env.get('SSH_CONNECTION') or env.get('SSH_TTY'):
        return False
    if sys.platform in ('darwin', 'win32', 'cygwin'):
        return True
    return bool(env.get('DISPLAY') or env.get('WAYLAND_DISPLAY'))


def launch_browser(url: str, browser_path: Optional[str] = None) -> bool:
    """
    Open a URL in a browser.

    Args:
        url: URL to open
        browser_path: Optional path to a browser executable (None = system default)

    Returns:
        True if the launch appears to have succeeded, False otherwise
    """
    try:
        if browser_path:
            return _launch_custom_browser(url, browser_path)
        return _launch_default_browser(url)
    except Exception as e:
        print(f'Warning: Failed to launch browser: {e}', file=sys.stderr)
        return False


def _spawn_detached(argv: list) -> subprocess.Popen:
    """Start argv with stdin/stdout/stderr on the null device, in its own session."""
    return subprocess.Popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _launch_default_browser(url: str) -> bool:
    """Open the URL with the system default browser via an isolated child."""
    if not sys.executable:
        # No interpreter path (unusual embedding); fall back to in-process
        return webbrowser.open(url)

    try:
        proc = _spawn_detached([sys.executable, '-I', '-c', _DEFAULT_LAUNCHER, url])
    except OSError as e:
        print(f'Warning: Failed to start browser launcher: {e}', file=sys.stderr)
        return False

    try:
        return proc.wait(timeout=_LAUNCH_WAIT) == 0
    except subprocess.TimeoutExpired:
        return True  # still running: treat as launched


def _launch_custom_browser(url: str, browser_path: str) -> bool:
    """
    Open the URL with a specific browser executable.

    Reporting is best effort: a launcher that fails within _LAUNCH_WAIT is
    reported; one that fails later is not observed.
    """
    try:
        # No shell (prevents injection); the child's output cannot reach our stdout
        proc = _spawn_detached([browser_path, url])
    except Exception as e:
        print(f'Warning: Failed to launch {browser_path}: {e}', file=sys.stderr)
        return False
    try:
        return proc.wait(timeout=_LAUNCH_WAIT) == 0
    except subprocess.TimeoutExpired:
        return True  # still running: treat as launched

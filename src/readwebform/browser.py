"""
Cross-platform browser launching for readwebform.

The CLI's contract is that stdout carries only the JSON result, so anything
a browser process prints must never reach our stdout. Custom launchers get
their standard streams pointed at the null device; for the system default
browser our own stdout file descriptor is redirected while webbrowser spawns
it, so the child inherits the null device.
"""

import contextlib
import os
import subprocess
import sys
import webbrowser
from typing import Optional


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


@contextlib.contextmanager
def _stdout_to_devnull():
    """Temporarily point file descriptor 1 at the null device."""
    try:
        sys.stdout.flush()
        stdout_fd = sys.stdout.fileno()
    except (AttributeError, ValueError, OSError):
        # No real stdout (e.g. captured by a test harness); nothing to protect
        yield
        return

    saved_fd = os.dup(stdout_fd)
    devnull_fd = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull_fd, stdout_fd)
        yield
    finally:
        os.dup2(saved_fd, stdout_fd)
        os.close(saved_fd)
        os.close(devnull_fd)


def _launch_default_browser(url: str) -> bool:
    """Open the URL with the system default browser."""
    try:
        with _stdout_to_devnull():
            return webbrowser.open(url)
    except Exception as e:
        print(f'Warning: webbrowser.open() failed: {e}', file=sys.stderr)
        return False


def _launch_custom_browser(url: str, browser_path: str) -> bool:
    """Open the URL with a specific browser executable."""
    try:
        # No shell (prevents injection); detach the child's standard streams
        # and session so its output and lifetime are independent of ours.
        subprocess.Popen(
            [browser_path, url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception as e:
        print(f'Warning: Failed to launch {browser_path}: {e}', file=sys.stderr)
        return False

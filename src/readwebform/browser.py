"""
Cross-platform browser launching for readwebform.
"""

import subprocess
import sys
import webbrowser
from typing import Optional


def launch_browser(url: str, browser_path: Optional[str] = None) -> bool:
    """
    Launch web browser to open URL.

    Args:
        url: URL to open
        browser_path: Optional path to browser executable (None = system default)

    Returns:
        True if successful, False otherwise
    """
    try:
        if browser_path:
            # Use specified browser executable
            return _launch_custom_browser(url, browser_path)
        else:
            # Use system default browser
            return _launch_default_browser(url)
    except Exception as e:
        print(f'Warning: Failed to launch browser: {e}', file=sys.stderr)
        return False


def _launch_default_browser(url: str) -> bool:
    """
    Launch system default browser.

    Args:
        url: URL to open

    Returns:
        True if successful, False otherwise
    """
    try:
        # webbrowser.open() is cross-platform
        return webbrowser.open(url)
    except Exception as e:
        print(f'Warning: webbrowser.open() failed: {e}', file=sys.stderr)
        return False


def _launch_custom_browser(url: str, browser_path: str) -> bool:
    """
    Launch specific browser executable.

    Args:
        url: URL to open
        browser_path: Path to browser executable

    Returns:
        True if successful, False otherwise
    """
    try:
        # Direct execution without shell to prevent command injection
        # This is secure on all platforms - shell=True was a security risk
        subprocess.Popen([browser_path, url])
        return True
    except Exception as e:
        print(f'Warning: Failed to launch {browser_path}: {e}', file=sys.stderr)
        return False

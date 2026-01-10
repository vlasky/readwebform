"""
Output formatting for readwebform (JSON and environment variables).
"""

import json
import re
import shlex
import sys
from typing import Dict, Optional


def format_json_output(
    fields: Dict[str, str],
    files: Dict[str, Dict[str, str]],
    success: bool = True,
    error: Optional[str] = None
) -> str:
    """
    Format form data as JSON with consistent envelope.

    Args:
        fields: Dictionary of field names to values
        files: Dictionary of file field names to file metadata
        success: Whether the form submission was successful
        error: Error message if not successful (e.g., 'timeout')

    Returns:
        JSON string with consistent schema:
        {
            "success": bool,
            "fields": {...},
            "files": {...},
            "error": string | null
        }
    """
    output = {
        'success': success,
        'fields': fields if fields is not None else {},
        'files': files if files is not None else {},
        'error': error
    }
    return json.dumps(output, indent=2, ensure_ascii=False)


def format_env_output(fields: Dict[str, str], prefix: str = 'WEBFORM_') -> str:
    """
    Format form data as shell environment variable exports.

    Args:
        fields: Dictionary of field names to values
        prefix: Prefix for environment variable names

    Returns:
        Shell export statements
    """
    lines = []
    skipped = []

    for name, value in fields.items():
        var_name = prefix + name.upper()

        # Validate variable name
        if not is_valid_var_name(var_name):
            skipped.append(name)
            continue

        # Sanitize value
        sanitized_value = sanitize_env_value(value)

        # Use shlex.quote for safe shell escaping
        quoted_value = shlex.quote(sanitized_value)

        lines.append(f'export {var_name}={quoted_value}')

    if skipped:
        print(
            f'Warning: Skipped invalid variable names: {", ".join(skipped)}',
            file=sys.stderr
        )

    # Add disclaimer
    disclaimer = '''# WARNING: Environment files are for trusted local use only.
# Do not source envfiles generated from untrusted form data.
'''
    return disclaimer + '\n'.join(lines)


def is_valid_var_name(name: str) -> bool:
    """
    Check if a string is a valid shell variable name.

    Args:
        name: Variable name to check

    Returns:
        True if valid, False otherwise
    """
    # Must start with letter or underscore, followed by letters, digits, or underscores
    return bool(re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', name))


def sanitize_env_value(value: str) -> str:
    """
    Sanitize value for use in environment variable.

    Args:
        value: Original value

    Returns:
        Sanitized value
    """
    # Convert newlines to literal \n
    value = value.replace('\n', '\\n')
    value = value.replace('\r', '')

    # Remove control characters (except tab)
    # Control characters are ASCII 0-31 (except tab=9) and 127
    value = ''.join(char for char in value if char == '\t' or not (ord(char) < 32 or ord(char) == 127))

    return value


def write_env_file(filepath: str, fields: Dict[str, str], prefix: str = 'WEBFORM_'):
    """
    Write environment variables to file.

    Args:
        filepath: Path to output file
        fields: Dictionary of field names to values
        prefix: Prefix for environment variable names
    """
    content = format_env_output(fields, prefix)
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

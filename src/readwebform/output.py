"""
Output formatting for readwebform (JSON and environment variables).
"""

import json
import os
import re
import shlex
import sys
from typing import Any, Dict, List, Optional, Union

DEFAULT_ENV_PREFIX = 'WEBFORM_'

_VAR_NAME_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def format_json_output(
    fields: Optional[Dict[str, Any]],
    files: Optional[Dict[str, Any]],
    success: bool = True,
    error: Optional[str] = None
) -> str:
    """
    Format form data as JSON with a consistent envelope.

    Args:
        fields: Field names to values (a list for repeated fields)
        files: File field names to metadata dicts (a list for repeated file fields)
        success: Whether the form submission was successful
        error: Error name if not successful (e.g. 'timeout', 'cancelled')

    Returns:
        JSON string with keys success, fields, files, error (always present)
    """
    output = {
        'success': success,
        'fields': fields if fields is not None else {},
        'files': files if files is not None else {},
        'error': error
    }
    return json.dumps(output, indent=2, ensure_ascii=False)


def env_var_name(name: str, prefix: str = DEFAULT_ENV_PREFIX) -> str:
    """
    Derive a shell variable name from a form field name.

    Characters that are not letters, digits or underscores become
    underscores, and the result is upper-cased: ``first-name`` becomes
    ``WEBFORM_FIRST_NAME``.
    """
    return prefix + re.sub(r'[^A-Za-z0-9_]', '_', name).upper()


def format_env_output(
    fields: Dict[str, Union[str, List[str]]],
    prefix: str = DEFAULT_ENV_PREFIX,
    files: Optional[Dict[str, Any]] = None
) -> str:
    """
    Format form data as shell ``export`` statements.

    A repeated field (checkbox group, multi-select) produces
    ``PREFIX_NAME`` with the values joined by newlines (rendered as the
    literal two characters ``\\n``), ``PREFIX_NAME_COUNT`` and
    ``PREFIX_NAME_1`` .. ``PREFIX_NAME_N``.

    Uploaded files produce ``PREFIX_NAME_PATH`` and ``PREFIX_NAME_FILENAME``
    (indexed in the same way when repeated).

    Args:
        fields: Field names to values
        prefix: Prefix for variable names
        files: Optional file metadata as produced by the server

    Returns:
        Shell export statements preceded by a warning comment
    """
    lines: List[str] = []
    seen: Dict[str, str] = {}
    skipped: List[str] = []

    def emit(var_name: str, value: str, source: str) -> None:
        if not is_valid_var_name(var_name):
            skipped.append(source)
            return
        if var_name in seen and seen[var_name] != source:
            print(f'Warning: fields {seen[var_name]!r} and {source!r} both map to '
                  f'{var_name}; the latter wins', file=sys.stderr)
        seen[var_name] = source
        lines.append(f'export {var_name}={shlex.quote(sanitize_env_value(value))}')

    for name, value in fields.items():
        var_name = env_var_name(name, prefix)
        if isinstance(value, list):
            emit(var_name, '\n'.join(value), name)
            emit(var_name + '_COUNT', str(len(value)), name)
            for i, item in enumerate(value, 1):
                emit(f'{var_name}_{i}', item, name)
        else:
            emit(var_name, value, name)

    for name, meta in (files or {}).items():
        var_name = env_var_name(name, prefix)
        if isinstance(meta, list):
            emit(var_name + '_COUNT', str(len(meta)), name)
            for i, item in enumerate(meta, 1):
                emit(f'{var_name}_{i}_PATH', item['path'], name)
                emit(f'{var_name}_{i}_FILENAME', item['filename'], name)
        else:
            emit(var_name + '_PATH', meta['path'], name)
            emit(var_name + '_FILENAME', meta['filename'], name)

    if skipped:
        print(f'Warning: Skipped invalid variable names: {", ".join(skipped)}', file=sys.stderr)

    disclaimer = ('# WARNING: Environment files are for trusted local use only.\n'
                  '# Do not source envfiles generated from untrusted form data.\n')
    return disclaimer + '\n'.join(lines)


def is_valid_var_name(name: str) -> bool:
    """Check whether a string is a valid shell variable name."""
    return bool(_VAR_NAME_RE.match(name))


def sanitize_env_value(value: str) -> str:
    """
    Sanitize a value for a single-line environment variable assignment.

    Newlines become the literal characters ``\\n``, carriage returns are
    dropped, and other control characters (except tab) are removed.
    """
    value = value.replace('\r', '').replace('\n', '\\n')
    return ''.join(ch for ch in value if ch == '\t' or not (ord(ch) < 32 or ord(ch) == 127))


def write_env_file(filepath: str, fields: Dict[str, Union[str, List[str]]],
                   prefix: str = DEFAULT_ENV_PREFIX,
                   files: Optional[Dict[str, Any]] = None) -> None:
    """
    Write environment variable exports to a file readable only by the owner.

    Args:
        filepath: Output path (created or truncated with mode 0600)
        fields: Field names to values
        prefix: Prefix for variable names
        files: Optional file metadata
    """
    content = format_env_output(fields, prefix, files)
    fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        # The mode above only applies to a newly created file
        os.fchmod(fd, 0o600)
    except (AttributeError, OSError):
        pass
    with os.fdopen(fd, 'w', encoding='utf-8') as f:
        f.write(content)

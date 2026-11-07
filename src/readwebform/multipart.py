"""
Multipart form data parser for readwebform.

Implements RFC 7578 (multipart/form-data) parsing without using the deprecated
cgi.FieldStorage module.
"""

import os
import re
import urllib.parse
from typing import Optional, Union


class FormParseError(ValueError):
    """Raised when a request body cannot be parsed."""


class SizeLimitExceeded(ValueError):
    """Raised when an upload exceeds a configured size limit."""


class UploadedFile:
    """Represents an uploaded file held in memory before it is saved."""

    def __init__(self, filename: str, content: bytes, content_type: str):
        self.filename = filename
        self.content = content
        self.content_type = content_type
        self.size = len(content)


class FormData:
    """Represents parsed form data.

    Fields with the same name (e.g. multiple checkboxes or a multi-select) are
    stored as lists. Single values are stored as strings. Files with the same
    name (e.g. ``<input type="file" multiple>``) are stored as lists of
    UploadedFile objects.
    """

    def __init__(self):
        self.fields: dict[str, Union[str, list[str]]] = {}
        self.files: dict[str, Union[UploadedFile, list[UploadedFile]]] = {}

    def add_field(self, name: str, value: str) -> None:
        """Add a field value, converting to a list if the name already exists."""
        if name in self.fields:
            existing = self.fields[name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                self.fields[name] = [existing, value]
        else:
            self.fields[name] = value

    def add_file(self, name: str, uploaded_file: UploadedFile) -> None:
        """Add an uploaded file, converting to a list if the name already exists."""
        if name in self.files:
            existing = self.files[name]
            if isinstance(existing, list):
                existing.append(uploaded_file)
            else:
                self.files[name] = [existing, uploaded_file]
        else:
            self.files[name] = uploaded_file

    def iter_files(self):
        """Yield (name, UploadedFile) pairs, flattening repeated file fields."""
        for name, value in self.files.items():
            if isinstance(value, list):
                for item in value:
                    yield name, item
            else:
                yield name, value


def parse_size_limit(limit_str: Optional[str]) -> Optional[int]:
    """
    Parse a size limit string to bytes.

    Examples:
        "5M" -> 5242880
        "200K" -> 204800
        "1G" -> 1073741824

    Args:
        limit_str: Size limit string (e.g. "5M", "200K"), or None for no limit

    Returns:
        Size in bytes, or None if no limit was given

    Raises:
        ValueError: If the string is not a valid size
    """
    if limit_str is None or limit_str.strip() == '':
        return None

    limit_str = limit_str.strip().upper()
    multipliers = {
        'K': 1024,
        'M': 1024 * 1024,
        'G': 1024 * 1024 * 1024,
    }

    match = re.match(r'^(\d+)([KMG])?$', limit_str)
    if not match:
        raise ValueError(f'Invalid size limit format: {limit_str}')

    value = int(match.group(1))
    unit = match.group(2)

    if unit:
        return value * multipliers[unit]
    return value


def parse_multipart(
    body: bytes,
    content_type: str,
    max_file_size: Optional[int] = None,
    max_total_size: Optional[int] = None
) -> FormData:
    """
    Parse a multipart/form-data request body.

    Args:
        body: Request body as bytes
        content_type: Content-Type header value
        max_file_size: Maximum size for an individual file (None = no limit)
        max_total_size: Maximum total body size (None = no limit)

    Returns:
        FormData instance

    Raises:
        SizeLimitExceeded: If a size limit is exceeded
        FormParseError: If the body cannot be parsed
    """
    if max_total_size is not None and len(body) > max_total_size:
        raise SizeLimitExceeded(f'Total upload size {len(body)} exceeds limit {max_total_size}')

    boundary = extract_boundary(content_type)
    if not boundary:
        raise FormParseError('No boundary found in Content-Type header')

    form_data = FormData()

    for part in split_multipart_body(body, boundary):
        headers, content = parse_part(part)
        disposition = headers.get('content-disposition', '')
        params = parse_header_params(disposition)

        name = params.get('name')
        if name is None:
            # RFC 7578 requires a name; ignore parts without one
            continue

        filename = params.get('filename')
        if filename is not None:
            if max_file_size is not None and len(content) > max_file_size:
                raise SizeLimitExceeded(
                    f'File {filename} size {len(content)} exceeds limit {max_file_size}'
                )
            content_type_part = headers.get('content-type', 'application/octet-stream')
            form_data.add_file(name, UploadedFile(filename, content, content_type_part))
        else:
            # Form fields are UTF-8 text; undecodable bytes are replaced rather than dropped
            form_data.add_field(name, content.decode('utf-8', errors='replace'))

    return form_data


def parse_urlencoded(body: bytes) -> FormData:
    """
    Parse an application/x-www-form-urlencoded request body.

    Args:
        body: Request body as bytes

    Returns:
        FormData instance

    Raises:
        FormParseError: If the body is not valid UTF-8
    """
    form_data = FormData()
    try:
        text = body.decode('utf-8')
    except UnicodeDecodeError as e:
        raise FormParseError(f'Form body is not valid UTF-8: {e}') from e

    parsed = urllib.parse.parse_qs(text, keep_blank_values=True)
    for key, values in parsed.items():
        for value in values:
            form_data.add_field(key, value)

    return form_data


def extract_boundary(content_type: str) -> Optional[str]:
    """
    Extract the boundary parameter from a Content-Type header.

    Args:
        content_type: Content-Type header value

    Returns:
        Boundary string or None
    """
    params = parse_header_params(content_type)
    boundary = params.get('boundary')
    if boundary:
        return boundary
    return None


_PARAM_RE = re.compile(
    r';\s*([A-Za-z0-9_.*-]+)\s*=\s*(?:"((?:[^"\\]|\\.)*)"|([^;]*))'
)


def parse_header_params(header: str) -> dict[str, str]:
    """
    Parse the ``; key=value`` parameters of a header such as Content-Disposition.

    Parameters are only recognised after a ``;`` so ``filename=`` can never be
    mistaken for ``name=``. Quoted values may contain backslash escapes. A
    ``key*=charset''value`` (RFC 5987) parameter is decoded and stored under
    ``key``.

    Args:
        header: Full header value, e.g. ``form-data; name="f"; filename="a.txt"``

    Returns:
        Dict of lower-cased parameter names to values
    """
    params: dict[str, str] = {}
    for match in _PARAM_RE.finditer(header):
        key = match.group(1).lower()
        if match.group(2) is not None:
            value = re.sub(r'\\(.)', r'\1', match.group(2))
        else:
            value = (match.group(3) or '').strip()

        if key.endswith('*'):
            key = key[:-1]
            value = _decode_rfc5987(value)
            params[key] = value
        elif key not in params:
            params[key] = value
    return params


def _decode_rfc5987(value: str) -> str:
    """Decode a ``charset'lang'percent-encoded`` extended parameter value."""
    parts = value.split("'", 2)
    if len(parts) == 3:
        charset, _, encoded = parts
        try:
            return urllib.parse.unquote(encoded, encoding=charset or 'utf-8', errors='replace')
        except LookupError:
            return urllib.parse.unquote(encoded, errors='replace')
    return urllib.parse.unquote(value, errors='replace')


def split_multipart_body(body: bytes, boundary: str) -> list[bytes]:
    """
    Split a multipart body into its parts (headers and content, undecoded).

    A delimiter is only recognised at the start of a line, so a boundary string
    that happens to occur inside a part's content is left untouched. Exactly the
    CRLF (or LF) that precedes each delimiter is consumed; all other bytes of a
    part are preserved.

    Args:
        body: Request body
        boundary: Boundary string from the Content-Type header

    Returns:
        List of raw parts
    """
    delimiter = re.compile(
        rb'(?:^|\r?\n)--' + re.escape(boundary.encode('utf-8')) + rb'(--)?[ \t]*(?:\r?\n|$)'
    )

    matches = list(delimiter.finditer(body))
    parts = []
    for i, match in enumerate(matches):
        if match.group(1):  # closing delimiter --boundary--
            break
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        parts.append(body[start:end])
    return parts


def parse_part(part: bytes) -> tuple[dict[str, str], bytes]:
    """
    Parse a single multipart part into headers and content.

    Args:
        part: Raw part (headers, blank line, content)

    Returns:
        Tuple of (headers_dict, content). Header names are lower-cased. Header
        values are decoded as UTF-8, which is how browsers send non-ASCII
        filenames.
    """
    crlf_idx = part.find(b'\r\n\r\n')
    lf_idx = part.find(b'\n\n')
    if crlf_idx != -1 and (lf_idx == -1 or crlf_idx <= lf_idx):
        header_data, content = part[:crlf_idx], part[crlf_idx + 4:]
    elif lf_idx != -1:
        header_data, content = part[:lf_idx], part[lf_idx + 2:]
    else:
        header_data, content = part, b''

    headers: dict[str, str] = {}
    for line in header_data.decode('utf-8', errors='replace').splitlines():
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        headers[key.strip().lower()] = value.strip()

    return headers, content


def extract_value_from_header(header: str, param: str) -> Optional[str]:
    """
    Extract a single parameter value from a header such as Content-Disposition.

    Args:
        header: Header value (e.g. 'form-data; name="field1"; filename="test.txt"')
        param: Parameter name to extract (e.g. 'name', 'filename')

    Returns:
        Parameter value or None
    """
    return parse_header_params(header).get(param.lower())


def save_uploaded_file(uploaded_file: UploadedFile, directory: str) -> str:
    """
    Save an uploaded file to a directory, avoiding name collisions.

    Args:
        uploaded_file: UploadedFile instance
        directory: Target directory (must exist)

    Returns:
        Full path to the saved file
    """
    filename = sanitize_filename(uploaded_file.filename)
    base, ext = os.path.splitext(filename)
    counter = 0
    while True:
        candidate = filename if counter == 0 else f'{base}_{counter}{ext}'
        filepath = os.path.join(directory, candidate)
        try:
            # O_EXCL makes the collision check atomic
            fd = os.open(filepath, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            counter += 1
            continue
        with os.fdopen(fd, 'wb') as f:
            f.write(uploaded_file.content)
        return filepath


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to prevent directory traversal.

    Args:
        filename: Original filename from the client

    Returns:
        A safe basename
    """
    # Strip any directory components (handle both / and \ regardless of OS)
    filename = filename.replace('\\', '/')
    filename = os.path.basename(filename)
    # Replace any remaining problematic characters
    filename = re.sub(r'[^\w\s.-]', '_', filename)
    # Limit length
    if len(filename) > 255:
        filename = filename[:255]
    # Ensure not empty or a special directory name
    if not filename or filename in ('.', '..'):
        filename = 'upload'
    return filename

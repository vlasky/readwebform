"""
Multipart form data parser for readwebform.

Implements RFC 2388 (multipart/form-data) parsing without using deprecated cgi.FieldStorage.
"""

import re
import tempfile
from typing import Dict, List, Optional, Tuple, Union
from email.parser import BytesParser
from email.message import Message


class UploadedFile:
    """Represents an uploaded file."""

    def __init__(self, filename: str, content: bytes, content_type: str):
        self.filename = filename
        self.content = content
        self.content_type = content_type
        self.size = len(content)


class FormData:
    """Represents parsed form data.

    Fields with the same name (e.g., multiple checkboxes or multi-select) are
    stored as lists. Single values are stored as strings for backward compatibility.
    Files with the same name are stored as lists of UploadedFile objects.
    """

    def __init__(self):
        self.fields: Dict[str, Union[str, List[str]]] = {}
        self.files: Dict[str, Union[UploadedFile, List[UploadedFile]]] = {}

    def add_field(self, name: str, value: str) -> None:
        """Add a field value, converting to list if name already exists."""
        if name in self.fields:
            existing = self.fields[name]
            if isinstance(existing, list):
                existing.append(value)
            else:
                self.fields[name] = [existing, value]
        else:
            self.fields[name] = value

    def add_file(self, name: str, uploaded_file: 'UploadedFile') -> None:
        """Add an uploaded file, converting to list if name already exists."""
        if name in self.files:
            existing = self.files[name]
            if isinstance(existing, list):
                existing.append(uploaded_file)
            else:
                self.files[name] = [existing, uploaded_file]
        else:
            self.files[name] = uploaded_file


def parse_size_limit(limit_str: Optional[str]) -> Optional[int]:
    """
    Parse size limit string to bytes.

    Examples:
        "5M" -> 5242880
        "200K" -> 204800
        "1G" -> 1073741824

    Args:
        limit_str: Size limit string (e.g., "5M", "200K")

    Returns:
        Size in bytes or None if no limit
    """
    if not limit_str:
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
    Parse multipart/form-data request body.

    Args:
        body: Request body as bytes
        content_type: Content-Type header value
        max_file_size: Maximum size for individual files
        max_total_size: Maximum total size (checked before parsing)

    Returns:
        FormData instance

    Raises:
        ValueError: If parsing fails or size limits exceeded
    """
    # Check total size limit
    if max_total_size and len(body) > max_total_size:
        raise ValueError(f'Total upload size {len(body)} exceeds limit {max_total_size}')

    # Extract boundary from content type
    boundary = extract_boundary(content_type)
    if not boundary:
        raise ValueError('No boundary found in Content-Type header')

    # Parse parts
    form_data = FormData()
    parts = split_multipart_body(body, boundary)

    for part in parts:
        if not part:
            continue

        headers, content = parse_part(part)
        disposition = headers.get('content-disposition', '')

        # Extract field name and filename
        name = extract_value_from_header(disposition, 'name')
        filename = extract_value_from_header(disposition, 'filename')

        if filename:
            # File upload
            if max_file_size and len(content) > max_file_size:
                raise ValueError(
                    f'File {filename} size {len(content)} exceeds limit {max_file_size}'
                )

            content_type_part = headers.get('content-type', 'application/octet-stream')
            form_data.add_file(name, UploadedFile(filename, content, content_type_part))
        else:
            # Regular field - decode as UTF-8
            try:
                form_data.add_field(name, content.decode('utf-8'))
            except UnicodeDecodeError:
                # If decoding fails, store as empty string
                form_data.add_field(name, '')

    return form_data


def parse_urlencoded(body: bytes) -> FormData:
    """
    Parse application/x-www-form-urlencoded request body.

    Args:
        body: Request body as bytes

    Returns:
        FormData instance
    """
    from urllib.parse import parse_qs

    form_data = FormData()
    try:
        parsed = parse_qs(body.decode('utf-8'), keep_blank_values=True)
        # parse_qs returns lists - preserve multiple values for repeated fields
        for key, values in parsed.items():
            if len(values) == 1:
                form_data.fields[key] = values[0]
            else:
                form_data.fields[key] = values
    except UnicodeDecodeError:
        pass

    return form_data


def extract_boundary(content_type: str) -> Optional[str]:
    """
    Extract boundary from Content-Type header.

    Args:
        content_type: Content-Type header value

    Returns:
        Boundary string or None
    """
    match = re.search(r'boundary=([^;]+)', content_type, re.IGNORECASE)
    if match:
        boundary = match.group(1).strip()
        # Remove quotes if present
        if boundary.startswith('"') and boundary.endswith('"'):
            boundary = boundary[1:-1]
        return boundary
    return None


def split_multipart_body(body: bytes, boundary: str) -> List[bytes]:
    """
    Split multipart body into individual parts.

    Args:
        body: Request body
        boundary: Boundary string

    Returns:
        List of part bodies
    """
    # Boundary appears as --boundary between parts and --boundary-- at the end
    boundary_bytes = ('--' + boundary).encode('utf-8')
    end_boundary_bytes = ('--' + boundary + '--').encode('utf-8')

    # Split by boundary
    parts = body.split(boundary_bytes)

    # Remove first empty part and last part (after end boundary)
    if parts:
        parts = parts[1:]  # Skip preamble

    # Remove final boundary marker and strip leading CRLF from each part
    result = []
    for part in parts:
        if part.startswith(b'--'):  # End boundary
            break
        # Strip leading CRLF or LF
        part = part.lstrip(b'\r\n')
        if part:
            result.append(part)

    return result


def parse_part(part: bytes) -> Tuple[Dict[str, str], bytes]:
    """
    Parse a single multipart part into headers and content.

    Args:
        part: Part body

    Returns:
        Tuple of (headers_dict, content)
    """
    # Split headers and content (separated by \r\n\r\n or \n\n)
    if b'\r\n\r\n' in part:
        header_data, content = part.split(b'\r\n\r\n', 1)
    elif b'\n\n' in part:
        header_data, content = part.split(b'\n\n', 1)
    else:
        # No content, only headers
        header_data = part
        content = b''

    # Strip leading/trailing whitespace and newlines
    content = content.rstrip(b'\r\n')

    # Parse headers using email parser
    headers = {}
    try:
        # Add a dummy "From" header to make it a valid email message
        header_data = b'From: dummy\r\n' + header_data
        msg = BytesParser().parsebytes(header_data)
        for key in msg.keys():
            if key.lower() != 'from':
                headers[key.lower()] = msg[key]
    except Exception:
        pass

    return headers, content


def extract_value_from_header(header: str, param: str) -> Optional[str]:
    """
    Extract parameter value from Content-Disposition header.

    Args:
        header: Header value (e.g., 'form-data; name="field1"; filename="test.txt"')
        param: Parameter name to extract (e.g., 'name', 'filename')

    Returns:
        Parameter value or None
    """
    # Match param="value" or param=value
    pattern = rf'{param}=(?:"([^"]*)"|([^;\s]+))'
    match = re.search(pattern, header, re.IGNORECASE)
    if match:
        return match.group(1) or match.group(2)
    return None


def save_uploaded_file(uploaded_file: UploadedFile, directory: str) -> str:
    """
    Save uploaded file to directory.

    Args:
        uploaded_file: UploadedFile instance
        directory: Target directory

    Returns:
        Full path to saved file
    """
    import os
    # Sanitize filename
    filename = sanitize_filename(uploaded_file.filename)
    filepath = os.path.join(directory, filename)

    # Handle duplicate filenames
    if os.path.exists(filepath):
        base, ext = os.path.splitext(filename)
        counter = 1
        while os.path.exists(filepath):
            filename = f'{base}_{counter}{ext}'
            filepath = os.path.join(directory, filename)
            counter += 1

    # Write file
    with open(filepath, 'wb') as f:
        f.write(uploaded_file.content)

    return filepath


def sanitize_filename(filename: str) -> str:
    """
    Sanitize filename to prevent directory traversal.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename
    """
    import os
    # Strip any directory components (handle both / and \ regardless of OS)
    # Replace backslashes with forward slashes first
    filename = filename.replace('\\', '/')
    # Get basename to prevent directory traversal
    filename = os.path.basename(filename)
    # Replace any remaining problematic characters
    filename = re.sub(r'[^\w\s.-]', '_', filename)
    # Limit length
    if len(filename) > 255:
        filename = filename[:255]
    # Ensure not empty
    if not filename or filename in ('.', '..'):
        filename = 'upload'
    return filename

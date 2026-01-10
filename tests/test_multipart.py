"""
Tests for multipart form data parsing.
"""

import pytest
from readwebform.multipart import (
    parse_size_limit,
    parse_multipart,
    parse_urlencoded,
    extract_boundary,
    sanitize_filename,
    FormData,
    UploadedFile
)


class TestParseSizeLimit:
    """Test size limit parsing."""

    def test_parse_bytes(self):
        assert parse_size_limit('1024') == 1024
        assert parse_size_limit('500') == 500

    def test_parse_kilobytes(self):
        assert parse_size_limit('5K') == 5 * 1024
        assert parse_size_limit('10k') == 10 * 1024

    def test_parse_megabytes(self):
        assert parse_size_limit('5M') == 5 * 1024 * 1024
        assert parse_size_limit('10m') == 10 * 1024 * 1024

    def test_parse_gigabytes(self):
        assert parse_size_limit('1G') == 1024 * 1024 * 1024
        assert parse_size_limit('2g') == 2 * 1024 * 1024 * 1024

    def test_parse_none(self):
        assert parse_size_limit(None) is None
        assert parse_size_limit('') is None

    def test_parse_invalid(self):
        with pytest.raises(ValueError):
            parse_size_limit('invalid')
        with pytest.raises(ValueError):
            parse_size_limit('5X')


class TestExtractBoundary:
    """Test boundary extraction from Content-Type."""

    def test_extract_simple(self):
        content_type = 'multipart/form-data; boundary=----WebKitFormBoundary'
        boundary = extract_boundary(content_type)
        assert boundary == '----WebKitFormBoundary'

    def test_extract_quoted(self):
        content_type = 'multipart/form-data; boundary="----WebKitFormBoundary"'
        boundary = extract_boundary(content_type)
        assert boundary == '----WebKitFormBoundary'

    def test_extract_with_charset(self):
        content_type = 'multipart/form-data; charset=utf-8; boundary=abc123'
        boundary = extract_boundary(content_type)
        assert boundary == 'abc123'

    def test_extract_none(self):
        content_type = 'application/json'
        boundary = extract_boundary(content_type)
        assert boundary is None


class TestParseURLEncoded:
    """Test URL-encoded form parsing."""

    def test_parse_simple(self):
        body = b'name=John&email=john%40example.com'
        form_data = parse_urlencoded(body)
        assert form_data.fields['name'] == 'John'
        assert form_data.fields['email'] == 'john@example.com'

    def test_parse_empty(self):
        body = b''
        form_data = parse_urlencoded(body)
        assert form_data.fields == {}

    def test_parse_special_chars(self):
        body = b'message=Hello+World%21'
        form_data = parse_urlencoded(body)
        assert form_data.fields['message'] == 'Hello World!'


class TestParseMultipart:
    """Test multipart form data parsing."""

    def test_parse_simple_field(self):
        boundary = '----WebKitFormBoundary'
        body = b'''------WebKitFormBoundary\r
Content-Disposition: form-data; name="username"\r
\r
John Doe\r
------WebKitFormBoundary--\r
'''
        content_type = f'multipart/form-data; boundary={boundary}'
        form_data = parse_multipart(body, content_type)
        assert form_data.fields['username'] == 'John Doe'

    def test_parse_multiple_fields(self):
        boundary = '----Boundary'
        body = b'''------Boundary\r
Content-Disposition: form-data; name="name"\r
\r
Alice\r
------Boundary\r
Content-Disposition: form-data; name="email"\r
\r
alice@example.com\r
------Boundary--\r
'''
        content_type = f'multipart/form-data; boundary={boundary}'
        form_data = parse_multipart(body, content_type)
        assert form_data.fields['name'] == 'Alice'
        assert form_data.fields['email'] == 'alice@example.com'

    def test_parse_file_upload(self):
        boundary = '----Boundary'
        body = b'''------Boundary\r
Content-Disposition: form-data; name="file"; filename="test.txt"\r
Content-Type: text/plain\r
\r
Hello, World!\r
------Boundary--\r
'''
        content_type = f'multipart/form-data; boundary={boundary}'
        form_data = parse_multipart(body, content_type)
        assert 'file' in form_data.files
        assert form_data.files['file'].filename == 'test.txt'
        assert form_data.files['file'].content == b'Hello, World!'
        assert form_data.files['file'].content_type == 'text/plain'

    def test_exceed_total_size(self):
        boundary = '----Boundary'
        body = b'''------Boundary\r
Content-Disposition: form-data; name="data"\r
\r
Large data here\r
------Boundary--\r
'''
        content_type = f'multipart/form-data; boundary={boundary}'
        with pytest.raises(ValueError, match='exceeds limit'):
            parse_multipart(body, content_type, max_total_size=10)

    def test_exceed_file_size(self):
        boundary = '----Boundary'
        body = b'''------Boundary\r
Content-Disposition: form-data; name="file"; filename="large.txt"\r
Content-Type: text/plain\r
\r
This is a large file content that exceeds the limit\r
------Boundary--\r
'''
        content_type = f'multipart/form-data; boundary={boundary}'
        with pytest.raises(ValueError, match='exceeds limit'):
            parse_multipart(body, content_type, max_file_size=10)


class TestSanitizeFilename:
    """Test filename sanitization."""

    def test_sanitize_simple(self):
        assert sanitize_filename('test.txt') == 'test.txt'
        assert sanitize_filename('document.pdf') == 'document.pdf'

    def test_sanitize_path_traversal(self):
        assert sanitize_filename('../../../etc/passwd') == 'passwd'
        assert sanitize_filename('..\\..\\windows\\system32') == 'system32'

    def test_sanitize_special_chars(self):
        filename = sanitize_filename('file<>:"|?*.txt')
        assert '<' not in filename
        assert '>' not in filename
        assert ':' not in filename

    def test_sanitize_empty(self):
        assert sanitize_filename('') == 'upload'
        assert sanitize_filename('...') == '...'

    def test_sanitize_long_name(self):
        long_name = 'a' * 300 + '.txt'
        result = sanitize_filename(long_name)
        assert len(result) <= 255


class TestFormData:
    """Test FormData class."""

    def test_create_empty(self):
        form_data = FormData()
        assert form_data.fields == {}
        assert form_data.files == {}

    def test_add_fields(self):
        form_data = FormData()
        form_data.fields['name'] = 'John'
        form_data.fields['email'] = 'john@example.com'
        assert len(form_data.fields) == 2


class TestUploadedFile:
    """Test UploadedFile class."""

    def test_create_file(self):
        content = b'File content'
        uploaded = UploadedFile('test.txt', content, 'text/plain')
        assert uploaded.filename == 'test.txt'
        assert uploaded.content == content
        assert uploaded.content_type == 'text/plain'
        assert uploaded.size == len(content)

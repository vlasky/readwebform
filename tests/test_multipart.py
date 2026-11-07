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


class TestPayloadFidelity:
    """Regression tests for byte-exact multipart handling."""

    B = 'XYZ'
    CT = 'multipart/form-data; boundary=XYZ'

    def _part(self, headers, content):
        return f'--{self.B}\r\n{headers}\r\n\r\n'.encode() + content + b'\r\n'

    def _body(self, *parts):
        return b''.join(parts) + f'--{self.B}--\r\n'.encode()

    def test_trailing_newlines_in_file_are_preserved(self):
        body = self._body(self._part('Content-Disposition: form-data; name="f"; filename="t.txt"', b'hello\n\n'))
        result = parse_multipart(body, self.CT)
        assert result.files['f'].content == b'hello\n\n'

    def test_trailing_crlf_in_field_is_preserved(self):
        body = self._body(self._part('Content-Disposition: form-data; name="msg"', b'line1\r\n'))
        result = parse_multipart(body, self.CT)
        assert result.fields['msg'] == 'line1\r\n'

    def test_binary_content_ending_in_cr_or_lf_bytes(self):
        payload = bytes(range(256)) + b'\r\n\r\n\n\r'
        body = self._body(self._part('Content-Disposition: form-data; name="f"; filename="b.bin"', payload))
        result = parse_multipart(body, self.CT)
        assert result.files['f'].content == payload

    def test_boundary_text_inside_payload_is_not_a_delimiter(self):
        body = self._body(self._part('Content-Disposition: form-data; name="f"', b'abc--XYZxyz'))
        result = parse_multipart(body, self.CT)
        assert result.fields == {'f': 'abc--XYZxyz'}

    def test_empty_field_value(self):
        body = self._body(self._part('Content-Disposition: form-data; name="f"', b''))
        result = parse_multipart(body, self.CT)
        assert result.fields == {'f': ''}

    def test_lf_only_line_endings(self):
        body = b'--XYZ\nContent-Disposition: form-data; name="f"\n\nvalue\n--XYZ--\n'
        result = parse_multipart(body, self.CT)
        assert result.fields == {'f': 'value'}

    def test_utf8_filename(self):
        body = self._body(self._part('Content-Disposition: form-data; name="f"; filename="café.txt"', b'x'))
        result = parse_multipart(body, self.CT)
        assert result.files['f'].filename == 'café.txt'

    def test_filename_before_name_parameter(self):
        body = self._body(self._part('Content-Disposition: form-data; filename="x.txt"; name="f"', b'x'))
        result = parse_multipart(body, self.CT)
        assert list(result.files) == ['f']
        assert result.files['f'].filename == 'x.txt'

    def test_quoted_filename_with_escaped_quote(self):
        body = self._body(self._part(r'Content-Disposition: form-data; name="f"; filename="a\"b.txt"', b'x'))
        result = parse_multipart(body, self.CT)
        assert result.files['f'].filename == 'a"b.txt'

    def test_rfc5987_filename(self):
        body = self._body(self._part(
            "Content-Disposition: form-data; name=\"f\"; filename*=UTF-8''caf%C3%A9.txt", b'x'))
        result = parse_multipart(body, self.CT)
        assert result.files['f'].filename == 'café.txt'

    def test_repeated_file_field_becomes_list(self):
        body = self._body(
            self._part('Content-Disposition: form-data; name="docs"; filename="a.txt"', b'A'),
            self._part('Content-Disposition: form-data; name="docs"; filename="b.txt"', b'B'),
        )
        result = parse_multipart(body, self.CT)
        files = result.files['docs']
        assert isinstance(files, list)
        assert [f.filename for f in files] == ['a.txt', 'b.txt']
        assert [(n, f.content) for n, f in result.iter_files()] == [('docs', b'A'), ('docs', b'B')]

    def test_part_without_name_is_ignored(self):
        body = self._body(self._part('Content-Disposition: form-data', b'x'))
        result = parse_multipart(body, self.CT)
        assert result.fields == {} and result.files == {}

    def test_zero_file_size_limit_is_enforced(self):
        body = self._body(self._part('Content-Disposition: form-data; name="f"; filename="t.txt"', b'x'))
        with pytest.raises(ValueError):
            parse_multipart(body, self.CT, max_file_size=0)

    def test_quoted_boundary(self):
        body = self._body(self._part('Content-Disposition: form-data; name="f"', b'v'))
        result = parse_multipart(body, 'multipart/form-data; boundary="XYZ"')
        assert result.fields == {'f': 'v'}

    def test_urlencoded_repeated_values(self):
        result = parse_urlencoded(b'a=1&a=2&b=3')
        assert result.fields == {'a': ['1', '2'], 'b': '3'}

    def test_urlencoded_invalid_utf8_raises(self):
        with pytest.raises(ValueError):
            parse_urlencoded(b'a=\xff')

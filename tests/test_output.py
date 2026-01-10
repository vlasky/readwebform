"""
Tests for output formatting.
"""

import json
import pytest
from readwebform.output import (
    format_json_output,
    format_env_output,
    is_valid_var_name,
    sanitize_env_value
)


class TestFormatJSONOutput:
    """Test JSON output formatting."""

    def test_format_simple(self):
        fields = {'name': 'John', 'email': 'john@example.com'}
        files = {}
        output = format_json_output(fields, files)

        data = json.loads(output)
        assert data['success'] is True
        assert data['fields']['name'] == 'John'
        assert data['fields']['email'] == 'john@example.com'
        assert data['files'] == {}
        assert data['error'] is None

    def test_format_with_files(self):
        fields = {'name': 'Alice'}
        files = {
            'resume': {
                'filename': 'cv.pdf',
                'path': '/tmp/readwebform_abc/cv.pdf'
            }
        }
        output = format_json_output(fields, files)

        data = json.loads(output)
        assert data['success'] is True
        assert data['fields']['name'] == 'Alice'
        assert data['files']['resume']['filename'] == 'cv.pdf'
        assert data['files']['resume']['path'] == '/tmp/readwebform_abc/cv.pdf'
        assert data['error'] is None

    def test_format_unicode(self):
        fields = {'name': 'José', 'message': '你好'}
        files = {}
        output = format_json_output(fields, files)

        data = json.loads(output)
        assert data['success'] is True
        assert data['fields']['name'] == 'José'
        assert data['fields']['message'] == '你好'
        assert data['error'] is None

    def test_format_error_timeout(self):
        """Test JSON output for timeout error."""
        output = format_json_output({}, {}, success=False, error='timeout')

        data = json.loads(output)
        assert data['success'] is False
        assert data['fields'] == {}
        assert data['files'] == {}
        assert data['error'] == 'timeout'

    def test_format_error_with_none_fields(self):
        """Test that None fields/files are converted to empty dicts."""
        output = format_json_output(None, None, success=False, error='timeout')

        data = json.loads(output)
        assert data['success'] is False
        assert data['fields'] == {}
        assert data['files'] == {}
        assert data['error'] == 'timeout'

    def test_format_success_explicit(self):
        """Test explicit success=True parameter."""
        fields = {'name': 'Test'}
        output = format_json_output(fields, {}, success=True)

        data = json.loads(output)
        assert data['success'] is True
        assert data['error'] is None

    def test_schema_consistency(self):
        """Test that all keys are always present in output."""
        # Success case
        success_output = format_json_output({'a': 'b'}, {})
        success_data = json.loads(success_output)
        assert set(success_data.keys()) == {'success', 'fields', 'files', 'error'}

        # Error case
        error_output = format_json_output({}, {}, success=False, error='timeout')
        error_data = json.loads(error_output)
        assert set(error_data.keys()) == {'success', 'fields', 'files', 'error'}


class TestFormatEnvOutput:
    """Test environment variable output formatting."""

    def test_format_simple(self):
        fields = {'name': 'John', 'email': 'john@example.com'}
        output = format_env_output(fields)

        assert 'export WEBFORM_NAME=' in output
        assert 'export WEBFORM_EMAIL=' in output
        assert 'John' in output
        assert 'john@example.com' in output

    def test_format_custom_prefix(self):
        fields = {'name': 'Alice'}
        output = format_env_output(fields, prefix='FORM_')

        assert 'export FORM_NAME=' in output

    def test_format_uppercase_conversion(self):
        fields = {'user_name': 'Bob'}
        output = format_env_output(fields)

        assert 'WEBFORM_USER_NAME' in output

    def test_format_shell_escaping(self):
        fields = {'message': "Hello 'World'"}
        output = format_env_output(fields)

        # shlex.quote should escape the single quotes
        assert 'WEBFORM_MESSAGE=' in output

    def test_format_newlines(self):
        fields = {'text': 'Line 1\nLine 2\nLine 3'}
        output = format_env_output(fields)

        # Newlines should be converted to \n
        assert '\\n' in output

    def test_disclaimer_present(self):
        fields = {'name': 'Test'}
        output = format_env_output(fields)

        assert 'WARNING' in output
        assert 'trusted local use only' in output


class TestIsValidVarName:
    """Test variable name validation."""

    def test_valid_names(self):
        assert is_valid_var_name('VAR') is True
        assert is_valid_var_name('var') is True
        assert is_valid_var_name('_var') is True
        assert is_valid_var_name('VAR_NAME') is True
        assert is_valid_var_name('var123') is True
        assert is_valid_var_name('_123') is True

    def test_invalid_names(self):
        assert is_valid_var_name('') is False
        assert is_valid_var_name('123var') is False
        assert is_valid_var_name('var-name') is False
        assert is_valid_var_name('var.name') is False
        assert is_valid_var_name('var name') is False
        assert is_valid_var_name('var$name') is False


class TestSanitizeEnvValue:
    """Test environment value sanitization."""

    def test_sanitize_simple(self):
        assert sanitize_env_value('hello') == 'hello'
        assert sanitize_env_value('hello world') == 'hello world'

    def test_sanitize_newlines(self):
        result = sanitize_env_value('line1\nline2\nline3')
        assert result == 'line1\\nline2\\nline3'

    def test_sanitize_carriage_return(self):
        result = sanitize_env_value('line1\r\nline2')
        assert '\r' not in result
        assert '\\n' in result

    def test_sanitize_tabs_preserved(self):
        result = sanitize_env_value('col1\tcol2\tcol3')
        assert '\t' in result

    def test_sanitize_control_chars(self):
        # Control characters should be removed
        result = sanitize_env_value('text\x00with\x01control\x02chars')
        assert '\x00' not in result
        assert '\x01' not in result
        assert '\x02' not in result
        assert 'text' in result

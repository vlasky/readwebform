"""
Tests for output formatting.
"""

import json
import sys

import pytest

from readwebform.output import format_env_output, format_json_output, is_valid_var_name, sanitize_env_value


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


class TestRepeatedFieldsAndNames:
    def test_list_values(self):
        output = format_env_output({'colours': ['red', 'blue']})
        assert "export WEBFORM_COLOURS='red\\nblue'" in output
        assert 'export WEBFORM_COLOURS_COUNT=2' in output
        assert 'export WEBFORM_COLOURS_1=red' in output
        assert 'export WEBFORM_COLOURS_2=blue' in output

    def test_hyphenated_name_is_mapped(self):
        output = format_env_output({'first-name': 'Ann', 'e.mail': 'a@b'})
        assert 'export WEBFORM_FIRST_NAME=Ann' in output
        assert 'export WEBFORM_E_MAIL=a@b' in output

    def test_collision_warns_and_last_wins(self, capsys):
        output = format_env_output({'a-b': '1', 'a_b': '2'})
        assert output.count('export WEBFORM_A_B=') == 2
        assert 'both map to WEBFORM_A_B' in capsys.readouterr().err

    def test_files_exported(self):
        files = {'doc': {'filename': 'a.txt', 'path': '/tmp/x/a.txt'},
                 'pics': [{'filename': 'p1.png', 'path': '/tmp/x/p1.png'},
                          {'filename': 'p2.png', 'path': '/tmp/x/p2.png'}]}
        output = format_env_output({}, files=files)
        assert 'export WEBFORM_DOC_PATH=/tmp/x/a.txt' in output
        assert 'export WEBFORM_DOC_FILENAME=a.txt' in output
        assert 'export WEBFORM_PICS_COUNT=2' in output
        assert 'export WEBFORM_PICS_2_PATH=/tmp/x/p2.png' in output

    @pytest.mark.skipif(sys.platform == 'win32', reason='POSIX permission bits')
    def test_envfile_is_owner_only(self, tmp_path):
        import os
        import stat

        from readwebform.output import write_env_file
        path = tmp_path / 'secrets.env'
        write_env_file(str(path), {'token': 's3cret'})
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert "export WEBFORM_TOKEN=s3cret" in path.read_text()
        # Rewriting an existing world-readable file also ends up 0600
        os.chmod(path, 0o644)
        write_env_file(str(path), {'token': 'new'})
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600

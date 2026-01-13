"""
Tests for CLI argument parsing.
"""

import pytest
from readwebform.cli import ReadWebFormArgumentParser


class TestCLIParser:
    """Test command-line argument parsing."""

    def test_default_values(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args([])

        assert args.host == '127.0.0.1'
        assert args.timeout == 300
        assert args.reset_timeout_on_error is True
        assert args.json is True

    def test_html_inline(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--html', '<form></form>'])

        assert args.html == '<form></form>'

    def test_htmlfile(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--htmlfile', 'test.html'])

        assert args.htmlfile == 'test.html'

    def test_field_single(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--field', 'name:text'])

        assert args.field == ['name:text']

    def test_field_multiple(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--field', 'name:text', '--field', 'email:email'])

        assert len(args.field) == 2
        assert 'name:text' in args.field
        assert 'email:email' in args.field

    def test_title_and_text(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--title', 'My Form', '--text', 'Fill this out'])

        assert args.title == 'My Form'
        assert args.text == 'Fill this out'

    def test_host_and_port(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--host', '0.0.0.0', '--port', '8080'])

        assert args.host == '0.0.0.0'
        assert args.port == 8080

    def test_size_limits(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--max-file-size', '5M', '--max-total-size', '20M'])

        assert args.max_file_size == '5M'
        assert args.max_total_size == '20M'

    def test_timeout_settings(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--timeout', '600', '--reset-timeout-on-error', 'false'])

        assert args.timeout == 600
        assert args.reset_timeout_on_error is False

    def test_output_formats(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--envfile', 'vars.env'])

        assert args.envfile == 'vars.env'

    def test_print_env(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--print-env'])

        assert args.print_env is True

    def test_launch_browser_default(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--launch-browser'])

        assert args.launch_browser == ''

    def test_launch_browser_custom(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--launch-browser', '/usr/bin/firefox'])

        assert args.launch_browser == '/usr/bin/firefox'

    def test_no_submit_button(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--no-submit-button'])

        assert args.no_submit_button is True

    def test_url_file(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--url-file', '/tmp/url.txt'])

        assert args.url_file == '/tmp/url.txt'

    def test_mutually_exclusive_html_htmlfile(self):
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit):
            parser.parse_args(['--html', '<form></form>', '--htmlfile', 'test.html'])

    def test_mutually_exclusive_html_field(self):
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit):
            parser.parse_args(['--html', '<form></form>', '--field', 'name:text'])

    def test_mutually_exclusive_htmlfile_field(self):
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit):
            parser.parse_args(['--htmlfile', 'test.html', '--field', 'name:text'])

    def test_invalid_timeout(self):
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit):
            parser.parse_args(['--timeout', '0'])

    def test_invalid_port(self):
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit):
            parser.parse_args(['--port', '70000'])

    def test_bool_conversion_true(self):
        parser = ReadWebFormArgumentParser()
        for value in ['true', 'yes', 'y', '1', 'True', 'YES']:
            args = parser.parse_args(['--reset-timeout-on-error', value])
            assert args.reset_timeout_on_error is True

    def test_bool_conversion_false(self):
        parser = ReadWebFormArgumentParser()
        for value in ['false', 'no', 'n', '0', 'False', 'NO']:
            args = parser.parse_args(['--reset-timeout-on-error', value])
            assert args.reset_timeout_on_error is False

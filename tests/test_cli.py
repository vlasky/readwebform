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


class TestCLIValidation:
    """Regression tests for argument validation."""

    def test_usage_error_exits_with_code_8(self):
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(['--timeout', '0'])
        assert exc.value.code == 8

    def test_invalid_size_limit_rejected_at_parse_time(self):
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(['--max-file-size', '5X'])
        assert exc.value.code == 8

    def test_empty_html_counts_as_an_input_source(self):
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit):
            parser.parse_args(['--html', '', '--field', 'a:text'])

    def test_float_timeout(self):
        parser = ReadWebFormArgumentParser()
        assert parser.parse_args(['--timeout', '0.5']).timeout == 0.5

    def test_non_finite_timeout_rejected(self):
        parser = ReadWebFormArgumentParser()
        for value in ['inf', 'nan', '-inf']:
            with pytest.raises(SystemExit):
                parser.parse_args(['--timeout', value])

    def test_upload_dir_and_env_prefix(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args(['--upload-dir', '/tmp/up', '--env-prefix', 'FORM_'])
        assert args.upload_dir == '/tmp/up'
        assert args.env_prefix == 'FORM_'

    def test_invalid_env_prefix(self):
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit):
            parser.parse_args(['--env-prefix', '1BAD-'])


class TestNewOptions:
    def test_version_flag(self, capsys):
        from readwebform import __version__
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(['--version'])
        assert exc.value.code == 0
        assert capsys.readouterr().out.strip() == f'readwebform {__version__}'

    def test_launch_browser_defaults_to_auto(self):
        parser = ReadWebFormArgumentParser()
        args = parser.parse_args([])
        assert args.launch_browser is None
        assert args.no_launch_browser is False

    def test_no_launch_browser(self):
        parser = ReadWebFormArgumentParser()
        assert parser.parse_args(['--no-launch-browser']).no_launch_browser is True

    def test_launch_flags_are_exclusive(self):
        parser = ReadWebFormArgumentParser()
        with pytest.raises(SystemExit):
            parser.parse_args(['--launch-browser', '--no-launch-browser'])

    def test_submit_label(self):
        parser = ReadWebFormArgumentParser()
        assert parser.parse_args([]).submit_label == 'Submit'
        assert parser.parse_args(['--submit-label', 'Ship it']).submit_label == 'Ship it'

"""
Tests for core orchestration: output-file failures stay clean.
"""

import io
from contextlib import redirect_stderr

from readwebform import core
from readwebform.cli import ReadWebFormArgumentParser
from readwebform.multipart import FormData


def _args(*argv):
    return ReadWebFormArgumentParser().parse_args(
        ['--field', 'name:text', '--no-launch-browser', *argv])


def _successful_submission(monkeypatch):
    form = FormData()
    form.add_field('name', 'x')
    monkeypatch.setattr(
        core.FormServer, 'serve', lambda self, **k: (True, form, {}, False))


class TestEnvfileWriteFailure:
    """An unwritable --envfile path must be a clean error, not a traceback."""

    def test_missing_envfile_directory_is_clean_error(self, monkeypatch, tmp_path):
        _successful_submission(monkeypatch)
        args = _args('--envfile', str(tmp_path / 'no-such-dir' / 'out.env'))
        buf = io.StringIO()
        with redirect_stderr(buf):
            rc = core.run_readwebform(args)
        assert rc == core.EXIT_INVALID_ARGUMENT
        assert 'Traceback' not in buf.getvalue()
        assert 'Error' in buf.getvalue()

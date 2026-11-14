"""
End-to-end tests driven by a real browser (Chromium via Playwright).

The rest of the suite builds request bodies by hand. These tests check what a
browser actually sends: its multipart encoding, raw UTF-8 filenames, the empty
part for a file input with nothing selected, and client-side validation.

Skipped unless Playwright and its Chromium build are installed:

    pip install -e ".[e2e]" && playwright install chromium
"""

import threading
import time

import pytest

sync_api = pytest.importorskip('playwright.sync_api')

from readwebform import Field, read_webform_fields  # noqa: E402

WAIT = 30  # seconds; generous, these are liveness limits


@pytest.fixture(scope='module')
def browser():
    with sync_api.sync_playwright() as p:
        try:
            instance = p.chromium.launch()
        except sync_api.Error as e:
            pytest.skip(f'Chromium is not available: {e}')
        yield instance
        instance.close()


@pytest.fixture
def page(browser):
    context = browser.new_context()
    yield context.new_page()
    context.close()


class FormSession:
    """Run read_webform_fields() on a thread and expose its URL and result."""

    def __init__(self, fields, tmp_path, **kwargs):
        self.result = None
        self.upload_dir = tmp_path / 'uploads'
        url_file = tmp_path / 'url.txt'
        self._thread = threading.Thread(
            target=self._run,
            args=(fields,),
            kwargs=dict(timeout=WAIT * 2, url_file=str(url_file),
                        upload_dir=str(self.upload_dir), **kwargs),
            daemon=True,
        )
        self._thread.start()
        deadline = time.monotonic() + WAIT
        while not (url_file.exists() and url_file.read_text().strip()):
            assert time.monotonic() < deadline, 'server did not start'
            time.sleep(0.01)
        self.url = url_file.read_text().strip()

    def _run(self, fields, **kwargs):
        self.result = read_webform_fields(fields, **kwargs)

    def wait(self):
        self._thread.join(timeout=WAIT)
        assert not self._thread.is_alive(), 'read_webform_fields() did not return'
        assert self.result is not None
        return self.result


def test_text_fields_round_trip(page, tmp_path):
    session = FormSession([
        Field(name='who', type='text', label='Name', required=True),
        Field(name='notes', type='textarea', label='Notes'),
        Field(name='colours', type='select', label='Colours',
              options=['red', 'green', 'blue'], multiple=True),
        Field(name='agree', type='checkbox', label='I agree'),
    ], tmp_path, title='Details')

    page.goto(session.url)
    assert page.title() == 'Details'
    page.fill('[name=who]', 'Zoë O\'Brien 你好')
    page.fill('[name=notes]', 'line one\nline two\n')
    page.select_option('[name=colours]', ['red', 'blue'])
    page.check('[name=agree]')
    page.click('button[type=submit]:not([name=_cancel])')
    page.wait_for_load_state()

    result = session.wait()
    assert result.success is True
    assert result.fields['who'] == 'Zoë O\'Brien 你好'
    # Browsers normalise textarea line breaks to CRLF on submission
    assert result.fields['notes'] == 'line one\r\nline two\r\n'
    assert result.fields['colours'] == ['red', 'blue']
    assert 'agree' in result.fields
    assert not result.files


def test_multiple_files_utf8_filename_and_unselected_input(page, tmp_path):
    binary = bytes(range(256)) * 4 + b'\r\n--boundary\r\n\r\n'
    sources = tmp_path / 'sources'
    sources.mkdir()
    (sources / 'résumé 履歴書.txt').write_bytes(b'first\r\n\r\n')
    (sources / 'data.bin').write_bytes(binary)

    session = FormSession([
        Field(name='who', type='text'),
        Field(name='docs', type='file', multiple=True),
        Field(name='photo', type='file'),
    ], tmp_path)

    page.goto(session.url)
    page.fill('[name=who]', 'tester')
    page.set_input_files('[name=docs]', [str(sources / 'résumé 履歴書.txt'),
                                         str(sources / 'data.bin')])
    # "photo" is deliberately left with no file selected
    page.click('button[type=submit]:not([name=_cancel])')
    page.wait_for_load_state()

    result = session.wait()
    assert result.success is True
    assert result.fields['who'] == 'tester'

    docs = result.files['docs']
    assert isinstance(docs, list) and len(docs) == 2
    by_name = {f.filename: f for f in docs}
    assert set(by_name) == {'résumé 履歴書.txt', 'data.bin'}
    with open(by_name['résumé 履歴書.txt'].path, 'rb') as f:
        assert f.read() == b'first\r\n\r\n'
    with open(by_name['data.bin'].path, 'rb') as f:
        assert f.read() == binary
    assert by_name['data.bin'].size == len(binary)

    # An unselected file input is an empty field, not an uploaded file
    assert 'photo' not in result.files
    assert len(list(session.upload_dir.iterdir())) == 2


def test_cancel_skips_required_validation(page, tmp_path):
    session = FormSession([Field(name='who', type='text', required=True)], tmp_path)

    page.goto(session.url)
    page.click('button[name=_cancel]')
    page.wait_for_load_state()

    result = session.wait()
    assert result.success is False
    assert result.error == 'cancelled'


def test_required_field_blocks_submit_in_browser(page, tmp_path):
    session = FormSession([Field(name='who', type='text', required=True)], tmp_path)

    page.goto(session.url)
    page.click('button[type=submit]:not([name=_cancel])')
    # The browser refuses to submit; the form is still showing
    assert page.evaluate('document.querySelector("[name=who]").validity.valueMissing') is True
    assert page.locator('[name=who]').count() == 1

    page.fill('[name=who]', 'now filled in')
    page.click('button[type=submit]:not([name=_cancel])')
    page.wait_for_load_state()

    result = session.wait()
    assert result.success is True
    assert result.fields['who'] == 'now filled in'

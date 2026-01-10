"""
Tests for HTML parsing and validation.
"""

import pytest
from readwebform.parser import (
    validate_html,
    is_external_url,
    inject_csrf_token,
    wrap_html_fragment,
    escape_html
)


class TestValidateHTML:
    """Test HTML validation."""

    def test_valid_simple_form(self):
        html = '<form><input type="submit"></form>'
        valid, error = validate_html(html, warn_no_submit=False)
        assert valid is True
        assert error is None

    def test_no_form(self):
        html = '<div>No form here</div>'
        valid, error = validate_html(html, warn_no_submit=False)
        assert valid is False
        assert 'No <form> element found' in error

    def test_multiple_forms(self):
        html = '<form></form><form></form>'
        valid, error = validate_html(html, warn_no_submit=False)
        assert valid is False
        assert 'Multiple <form> elements' in error

    def test_external_action_rejected(self):
        html = '<form action="https://evil.com/steal"></form>'
        valid, error = validate_html(html, warn_no_submit=False)
        assert valid is False
        assert 'External form action URL rejected' in error

    def test_relative_action_allowed(self):
        html = '<form action="/submit"></form>'
        valid, error = validate_html(html, warn_no_submit=False)
        assert valid is True

    def test_empty_action_allowed(self):
        html = '<form action=""></form>'
        valid, error = validate_html(html, warn_no_submit=False)
        assert valid is True


class TestIsExternalURL:
    """Test external URL detection."""

    def test_http_url(self):
        assert is_external_url('http://example.com') is True

    def test_https_url(self):
        assert is_external_url('https://example.com') is True

    def test_protocol_relative_url(self):
        assert is_external_url('//example.com') is True

    def test_relative_url(self):
        assert is_external_url('/submit') is False

    def test_empty_url(self):
        assert is_external_url('') is False

    def test_fragment_url(self):
        assert is_external_url('#anchor') is False


class TestInjectCSRFToken:
    """Test CSRF token injection."""

    def test_inject_token(self):
        html = '<form><input name="test"></form>'
        result = inject_csrf_token(html, 'abc123', '/endpoint')
        assert '_csrf_token' in result
        assert 'value="abc123"' in result
        assert 'action="/endpoint"' in result

    def test_replace_action(self):
        html = '<form action="/old"><input name="test"></form>'
        result = inject_csrf_token(html, 'token', '/new')
        assert 'action="/new"' in result
        assert 'action="/old"' not in result

    def test_case_insensitive(self):
        html = '<FORM><INPUT name="test"></FORM>'
        result = inject_csrf_token(html, 'token', '/endpoint')
        assert '_csrf_token' in result.lower()


class TestWrapHTMLFragment:
    """Test HTML fragment wrapping."""

    def test_wrap_fragment(self):
        html = '<form><input type="submit"></form>'
        result = wrap_html_fragment(html)
        assert '<!DOCTYPE html>' in result
        assert '<html>' in result
        assert '<head>' in result
        assert '<body>' in result

    def test_with_title(self):
        html = '<form></form>'
        result = wrap_html_fragment(html, title='Test Form')
        assert '<title>Test Form</title>' in result

    def test_with_text(self):
        html = '<form></form>'
        result = wrap_html_fragment(html, text='Please fill out this form')
        assert '<p>Please fill out this form</p>' in result

    def test_complete_document_unchanged(self):
        html = '<!DOCTYPE html><html><head></head><body><form></form></body></html>'
        result = wrap_html_fragment(html)
        # Should not double-wrap
        assert result.count('<!DOCTYPE html>') == 1

    def test_escape_title_and_text(self):
        html = '<form></form>'
        result = wrap_html_fragment(html, title='<script>alert("xss")</script>', text='<b>Bold</b>')
        assert '<script>' not in result
        assert '&lt;script&gt;' in result
        assert '&lt;b&gt;' in result


class TestEscapeHTML:
    """Test HTML escaping."""

    def test_escape_basic(self):
        assert escape_html('<div>') == '&lt;div&gt;'
        assert escape_html('&') == '&amp;'
        assert escape_html('"test"') == '&quot;test&quot;'
        assert escape_html("'test'") == '&#x27;test&#x27;'

    def test_escape_empty(self):
        assert escape_html('') == ''
        assert escape_html(None) == ''

    def test_escape_combined(self):
        result = escape_html('<script>alert("xss")</script>')
        assert '<' not in result
        assert '>' not in result
        assert '&lt;script&gt;' in result

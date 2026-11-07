"""
Tests for HTML parsing and validation.
"""

from readwebform.parser import (
    escape_html,
    inject_csrf_token,
    is_external_url,
    validate_html,
    wrap_html_fragment,
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


class TestInjectCSRFTokenRewriting:
    """Regression tests for rewriting the form start tag."""

    def test_existing_method_get_is_replaced(self):
        result = inject_csrf_token('<form method="get"><input name="a"></form>', 'T', '/ep')
        assert result.count('method=') == 1
        assert 'method="POST"' in result
        assert 'action="/ep"' in result

    def test_other_attributes_preserved(self):
        html = '<form id="f" class="x y" enctype="multipart/form-data" novalidate><input name="a"></form>'
        result = inject_csrf_token(html, 'T', '/ep')
        assert 'id="f"' in result and 'class="x y"' in result
        assert 'enctype="multipart/form-data"' in result
        assert ' novalidate' in result

    def test_form_inside_comment_is_ignored(self):
        html = '<!-- <form> --><form><button>Submit</button></form>'
        result = inject_csrf_token(html, 'T', '/ep')
        assert result.startswith('<!-- <form> --><form action="/ep" method="POST">')
        assert result.count('_csrf_token') == 1

    def test_quoted_gt_in_attribute(self):
        html = '<form title="a>b"><button>Submit</button></form>'
        result = inject_csrf_token(html, 'T', '/ep')
        assert 'title="a&gt;b"' in result
        assert 'action="/ep"' in result
        assert '<button>Submit</button></form>' in result

    def test_multiline_document_offsets(self):
        html = '<!DOCTYPE html>\n<html>\n<body>\n  <p>x</p>\n  <form action="/old"\n        method="get">\n  <input name="a">\n  </form>\n</body>\n</html>'
        result = inject_csrf_token(html, 'T', '/ep')
        assert '/old' not in result
        assert '<form action="/ep" method="POST"><input type="hidden" name="_csrf_token" value="T">\n  <input name="a">' in result

    def test_token_is_escaped(self):
        result = inject_csrf_token('<form></form>', 'a"b', '/ep')
        assert 'value="a&quot;b"' in result

    def test_unicode_before_form(self):
        html = '<p>café ☕</p><form><input name="a"></form>'
        result = inject_csrf_token(html, 'T', '/ep')
        assert result.startswith('<p>café ☕</p><form action="/ep" method="POST">')


class TestWrapHTMLFragmentRegressions:
    def test_title_rendered_as_heading(self):
        result = wrap_html_fragment('<form></form>', title='My Title')
        assert '<h1>My Title</h1>' in result
        assert '<title>My Title</title>' in result

    def test_backslashes_in_text_with_complete_document(self):
        html = '<html><head></head><body><form></form></body></html>'
        result = wrap_html_fragment(html, title=r'C:\Users', text=r'Path C:\Users\Alice')
        assert r'<p>Path C:\Users\Alice</p><form>' in result
        assert r'<title>C:\Users</title>' in result

    def test_comment_form_in_complete_document(self):
        html = '<html><body><!-- <form> --><form></form></body></html>'
        result = wrap_html_fragment(html, text='hi')
        assert '<!-- <form> --><p>hi</p><form>' in result

    def test_cancel_button_style_present(self):
        result = wrap_html_fragment('<form></form>')
        assert 'button.cancel' in result

    def test_validation_ignores_commented_form(self):
        valid, _ = validate_html('<!-- <form></form> --><form><input type="submit"></form>',
                                 warn_no_submit=False)
        assert valid is True
        valid, _ = validate_html('<!-- <form></form> -->', warn_no_submit=False)
        assert valid is False

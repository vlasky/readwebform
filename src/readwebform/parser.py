"""
HTML parsing, validation, rewriting and page templating for readwebform.
"""

import html as html_mod
import re
import sys
from html.parser import HTMLParser
from typing import Optional


class FormDetector(HTMLParser):
    """
    Locate and inspect the <form> element(s) in an HTML document.

    Comments and CDATA are skipped by HTMLParser, so a ``<form>`` inside a
    comment is neither counted nor rewritten.
    """

    def __init__(self, source: str):
        super().__init__(convert_charrefs=True)
        self._line_starts = _line_starts(source)
        self.form_count = 0
        self.form_action: Optional[str] = None
        self.has_submit_button = False
        self.in_form = False
        # Position of the first form's start tag within the source
        self.form_start: Optional[int] = None
        self.form_tag_text: Optional[str] = None
        self.form_attrs: list[tuple[str, Optional[str]]] = []

    def _index(self) -> int:
        lineno, offset = self.getpos()
        return self._line_starts[lineno - 1] + offset

    def handle_starttag(self, tag: str, attrs: list):
        attrs_dict = dict(attrs)

        if tag == 'form':
            self.form_count += 1
            self.in_form = True
            if self.form_count == 1:
                self.form_action = attrs_dict.get('action') or ''
                self.form_start = self._index()
                self.form_tag_text = self.get_starttag_text()
                self.form_attrs = list(attrs)

        if self.in_form and tag in ('input', 'button'):
            default_type = 'text' if tag == 'input' else 'submit'
            if (attrs_dict.get('type') or default_type).lower() == 'submit':
                self.has_submit_button = True

    def handle_endtag(self, tag: str):
        if tag == 'form':
            self.in_form = False


def _line_starts(source: str) -> list[int]:
    """Character index at which each line starts; HTMLParser counts lines by '\\n'."""
    starts = [0]
    for i, ch in enumerate(source):
        if ch == '\n':
            starts.append(i + 1)
    return starts


def _detect(html: str) -> FormDetector:
    detector = FormDetector(html)
    detector.feed(html)
    detector.close()
    return detector


def validate_html(html: str, warn_no_submit: bool = True) -> tuple[bool, Optional[str]]:
    """
    Validate that HTML contains exactly one form element.

    Args:
        html: HTML content to validate
        warn_no_submit: Whether to warn on stderr if no submit button is found

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        detector = _detect(html)
    except Exception as e:
        return False, f'Failed to parse HTML: {e}'

    if detector.form_count == 0:
        return False, 'No <form> element found in HTML'
    if detector.form_count > 1:
        return False, f'Multiple <form> elements found ({detector.form_count}), exactly one required'

    if detector.form_action and is_external_url(detector.form_action):
        return False, f'External form action URL rejected: {detector.form_action}'

    if warn_no_submit and not detector.has_submit_button:
        print('Warning: No submit button found in form', file=sys.stderr)

    return True, None


def is_external_url(url: str) -> bool:
    """Check if a URL is external (absolute http(s) or protocol-relative)."""
    if not url:
        return False
    return bool(re.match(r'^\s*(https?:)?//', url, re.IGNORECASE))


def inject_csrf_token(html: str, csrf_token: str, endpoint: str) -> str:
    """
    Rewrite the form's start tag to post to our endpoint and add the CSRF field.

    Any existing ``action`` and ``method`` attributes are removed; other
    attributes (``enctype``, ``id``, ``class``...) are preserved. Attribute
    values are re-escaped, and the hidden CSRF input is inserted immediately
    after the start tag.

    Args:
        html: Original HTML content (must contain a <form>)
        csrf_token: CSRF token to inject
        endpoint: Server endpoint path

    Returns:
        Modified HTML
    """
    detector = _detect(html)
    if detector.form_start is None or detector.form_tag_text is None:
        return html

    attrs = [(k, v) for k, v in detector.form_attrs if k.lower() not in ('action', 'method')]
    attrs += [('action', endpoint), ('method', 'POST')]

    parts = ['<form']
    for key, value in attrs:
        if value is None:
            parts.append(f' {key}')
        else:
            parts.append(f' {key}="{html_mod.escape(value, quote=True)}"')
    new_tag = ''.join(parts) + '>'

    csrf_field = (f'<input type="hidden" name="_csrf_token" '
                  f'value="{html_mod.escape(csrf_token, quote=True)}">')

    start = detector.form_start
    end = start + len(detector.form_tag_text)
    return html[:start] + new_tag + csrf_field + html[end:]


PAGE_CSS = '''
        body {
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 600px;
            margin: 40px auto;
            padding: 20px;
            line-height: 1.6;
        }
        h1 {
            font-size: 1.5em;
            margin: 0 0 10px;
        }
        label {
            display: block;
            margin: 15px 0 5px;
            font-weight: 500;
        }
        input, textarea, select {
            width: 100%;
            padding: 8px;
            border: 1px solid #ccc;
            border-radius: 4px;
            box-sizing: border-box;
        }
        input[type="checkbox"], input[type="radio"] {
            width: auto;
            margin-right: 8px;
        }
        input[type="checkbox"] + label, input[type="radio"] + label {
            display: inline;
            font-weight: normal;
        }
        fieldset {
            border: none;
            padding: 0;
            margin: 15px 0 0;
        }
        legend {
            font-weight: 500;
            padding: 0;
        }
        button, input[type="submit"] {
            width: auto;
            margin-top: 20px;
            margin-right: 10px;
            padding: 10px 20px;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
        }
        button:hover, input[type="submit"]:hover {
            background: #0056b3;
        }
        button.cancel {
            background: #6c757d;
        }
        button.cancel:hover {
            background: #5a6268;
        }
'''


def render_page(content_html: str, title: Optional[str] = None, text: Optional[str] = None) -> str:
    """
    Wrap form markup in a complete, styled HTML document.

    Args:
        content_html: Markup to place in the body (normally a <form>)
        title: Page title; shown in the browser tab and as a heading above the form
        text: Instructional text shown above the form

    Returns:
        Complete HTML document
    """
    title_text = escape_html(title) if title else 'Form'
    heading = f'<h1>{escape_html(title)}</h1>\n    ' if title else ''
    paragraph = f'<p>{escape_html(text)}</p>\n    ' if text else ''

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title_text}</title>
    <style>{PAGE_CSS}    </style>
</head>
<body>
    {heading}{paragraph}{content_html}
</body>
</html>'''


def wrap_html_fragment(html: str, title: Optional[str] = None, text: Optional[str] = None) -> str:
    """
    Ensure HTML is a complete document, adding title and text if given.

    A fragment is wrapped in the standard page template. A complete document
    is left as-is except that a ``<title>`` is added if missing and the
    heading/text are inserted immediately before the form.

    Args:
        html: HTML content (fragment or complete document)
        title: Optional page title
        text: Optional instructional text

    Returns:
        Complete HTML document
    """
    if not re.search(r'<!DOCTYPE|<html', html, re.IGNORECASE):
        return render_page(html, title, text)

    if title and not re.search(r'<title[\s>]', html, re.IGNORECASE):
        html = re.sub(r'<head[^>]*>', lambda m: m.group(0) + f'<title>{escape_html(title)}</title>',
                      html, count=1, flags=re.IGNORECASE)

    prefix = ''
    if title:
        prefix += f'<h1>{escape_html(title)}</h1>'
    if text:
        prefix += f'<p>{escape_html(text)}</p>'
    if prefix:
        detector = _detect(html)
        if detector.form_start is not None:
            html = html[:detector.form_start] + prefix + html[detector.form_start:]

    return html


def escape_html(text: Optional[str]) -> str:
    """Escape HTML special characters (including quotes) in text."""
    if not text:
        return ''
    return html_mod.escape(text, quote=True)

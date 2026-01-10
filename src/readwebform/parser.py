"""
HTML parsing and validation for readwebform.
"""

import re
import sys
from html.parser import HTMLParser
from typing import Optional, Tuple


class FormDetector(HTMLParser):
    """HTML parser to detect and validate form elements."""

    def __init__(self):
        super().__init__()
        self.form_count = 0
        self.form_action: Optional[str] = None
        self.has_submit_button = False
        self.in_form = False

    def handle_starttag(self, tag: str, attrs: list):
        """Handle opening tags."""
        attrs_dict = dict(attrs)

        if tag == 'form':
            self.form_count += 1
            self.in_form = True
            self.form_action = attrs_dict.get('action', '')

        if self.in_form and tag == 'input':
            input_type = attrs_dict.get('type', 'text').lower()
            if input_type == 'submit':
                self.has_submit_button = True

        if self.in_form and tag == 'button':
            button_type = attrs_dict.get('type', 'submit').lower()
            if button_type == 'submit':
                self.has_submit_button = True

    def handle_endtag(self, tag: str):
        """Handle closing tags."""
        if tag == 'form':
            self.in_form = False


def validate_html(html: str, warn_no_submit: bool = True) -> Tuple[bool, Optional[str]]:
    """
    Validate HTML contains exactly one form element.

    Args:
        html: HTML content to validate
        warn_no_submit: Whether to warn if no submit button found

    Returns:
        Tuple of (is_valid, error_message)
    """
    detector = FormDetector()
    try:
        detector.feed(html)
    except Exception as e:
        return False, f'Failed to parse HTML: {e}'

    # Check form count
    if detector.form_count == 0:
        return False, 'No <form> element found in HTML'
    elif detector.form_count > 1:
        return False, f'Multiple <form> elements found ({detector.form_count}), exactly one required'

    # Check for external action URLs
    if detector.form_action and is_external_url(detector.form_action):
        return False, f'External form action URL rejected: {detector.form_action}'

    # Warn about missing submit button
    if warn_no_submit and not detector.has_submit_button:
        print('Warning: No submit button found in form', file=sys.stderr)

    return True, None


def is_external_url(url: str) -> bool:
    """Check if a URL is external (starts with http://, https://, //, etc.)."""
    if not url:
        return False
    url = url.strip()
    # Check for absolute URLs
    if re.match(r'^(https?:)?//', url, re.IGNORECASE):
        return True
    # Check for protocol-relative URLs pointing to other domains
    if url.startswith('//'):
        return True
    return False


def inject_csrf_token(html: str, csrf_token: str, endpoint: str) -> str:
    """
    Inject CSRF token into form and fix action attribute.

    Args:
        html: Original HTML content
        csrf_token: CSRF token to inject
        endpoint: Server endpoint path

    Returns:
        Modified HTML with CSRF token and corrected action
    """
    # Inject CSRF token as hidden field right after form opening tag
    csrf_field = f'<input type="hidden" name="_csrf_token" value="{csrf_token}">'

    # Find the form tag and inject the CSRF token after it
    def inject_token(match):
        form_tag = match.group(0)
        return form_tag + csrf_field

    html = re.sub(
        r'<form[^>]*>',
        inject_token,
        html,
        count=1,
        flags=re.IGNORECASE
    )

    # Fix or set the action attribute to point to our endpoint
    def fix_action(match):
        form_tag = match.group(0)
        # Remove existing action attribute if present
        form_tag = re.sub(r'\s+action\s*=\s*["\'][^"\']*["\']', '', form_tag, flags=re.IGNORECASE)
        form_tag = re.sub(r'\s+action\s*=\s*\S+', '', form_tag, flags=re.IGNORECASE)
        # Add our endpoint as the action
        form_tag = form_tag.rstrip('>') + f' action="{endpoint}" method="POST">'
        return form_tag

    html = re.sub(
        r'<form[^>]*>',
        fix_action,
        html,
        count=1,
        flags=re.IGNORECASE
    )

    return html


def wrap_html_fragment(html: str, title: Optional[str] = None, text: Optional[str] = None) -> str:
    """
    Wrap HTML fragment in a complete HTML document if needed.

    Args:
        html: HTML content (fragment or complete document)
        title: Optional page title
        text: Optional instructional text

    Returns:
        Complete HTML document
    """
    # Check if already a complete document
    if re.search(r'<!DOCTYPE|<html', html, re.IGNORECASE):
        # Insert title and text if provided and not already present
        if title and '<title>' not in html.lower():
            html = re.sub(
                r'(<head[^>]*>)',
                rf'\1<title>{escape_html(title)}</title>',
                html,
                count=1,
                flags=re.IGNORECASE
            )
        if text:
            # Try to insert before the form
            html = re.sub(
                r'(<form[^>]*>)',
                rf'<p>{escape_html(text)}</p>\1',
                html,
                count=1,
                flags=re.IGNORECASE
            )
        return html

    # Wrap fragment
    title_tag = f'<title>{escape_html(title)}</title>' if title else '<title>Form</title>'
    text_block = f'<p>{escape_html(text)}</p>' if text else ''

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {title_tag}
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 600px;
            margin: 40px auto;
            padding: 20px;
            line-height: 1.6;
        }}
        form {{
            background: #f5f5f5;
            padding: 20px;
            border-radius: 8px;
        }}
        input, textarea, select {{
            width: 100%;
            padding: 8px;
            margin: 8px 0;
            border: 1px solid #ddd;
            border-radius: 4px;
            box-sizing: border-box;
        }}
        button, input[type="submit"] {{
            background: #007bff;
            color: white;
            padding: 10px 20px;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            margin-top: 10px;
        }}
        button:hover, input[type="submit"]:hover {{
            background: #0056b3;
        }}
        label {{
            display: block;
            margin-top: 10px;
            font-weight: 500;
        }}
    </style>
</head>
<body>
    {text_block}
    {html}
</body>
</html>'''


def escape_html(text: str) -> str:
    """Escape HTML special characters."""
    if not text:
        return ''
    return (text
            .replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&#x27;'))

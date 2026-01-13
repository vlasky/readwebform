"""
Declarative form generation for readwebform.
"""

import urllib.parse
from typing import Dict, List, Optional


class FieldSpec:
    """Represents a parsed field specification."""

    def __init__(self, name: str, field_type: str, label: Optional[str] = None,
                 options: Optional[Dict[str, str]] = None):
        self.name = name
        self.field_type = field_type
        self.label = label or name
        self.options = options or {}

    @classmethod
    def parse(cls, spec: str) -> 'FieldSpec':
        """
        Parse field specification string.

        Format: name:type[:label][:options]
        Example: name:text:Full+Name:required,placeholder=Your+name

        Args:
            spec: Field specification string

        Returns:
            FieldSpec instance

        Raises:
            ValueError: If specification is invalid
        """
        parts = spec.split(':', 3)
        if len(parts) < 2:
            raise ValueError(f'Invalid field spec: {spec} (expected name:type[:label][:options])')

        name = parts[0].strip()
        field_type = parts[1].strip()
        label = None
        options = {}

        if len(parts) >= 3:
            label = urllib.parse.unquote_plus(parts[2])

        if len(parts) >= 4:
            options = cls._parse_options(parts[3])

        # Validate field type
        valid_types = {'text', 'email', 'password', 'number', 'date', 'url',
                      'textarea', 'select', 'checkbox', 'file'}
        if field_type not in valid_types:
            raise ValueError(f'Invalid field type: {field_type} (must be one of {valid_types})')

        return cls(name, field_type, label, options)

    @staticmethod
    def _parse_options(options_str: str) -> Dict[str, str]:
        """Parse options string into dictionary."""
        options = {}
        for option in options_str.split(','):
            option = option.strip()
            if not option:
                continue

            if '=' in option:
                key, value = option.split('=', 1)
                options[key.strip()] = urllib.parse.unquote_plus(value.strip())
            else:
                # Boolean flag (e.g., "required")
                options[option] = 'true'

        return options


def generate_form_html(fields: List[FieldSpec], add_submit_button: bool = True,
                       add_cancel_button: bool = True, cancel_label: str = 'Cancel') -> str:
    """
    Generate HTML form from field specifications.

    Args:
        fields: List of FieldSpec instances
        add_submit_button: Whether to add a submit button
        add_cancel_button: Whether to add a cancel button
        cancel_label: Label text for the cancel button

    Returns:
        HTML form string
    """
    # Check if any file fields exist
    has_file_field = any(f.field_type == 'file' for f in fields)
    enctype = ' enctype="multipart/form-data"' if has_file_field else ''

    form_parts = [f'<form method="POST"{enctype}>']

    for field in fields:
        form_parts.append(generate_field_html(field))

    if add_submit_button:
        form_parts.append('    <button type="submit">Submit</button>')

    if add_cancel_button:
        form_parts.append(f'    <button type="submit" name="_cancel" value="1" class="cancel" formnovalidate>{escape_html(cancel_label)}</button>')

    form_parts.append('</form>')

    return '\n'.join(form_parts)


def generate_field_html(field: FieldSpec) -> str:
    """
    Generate HTML for a single form field.

    Args:
        field: FieldSpec instance

    Returns:
        HTML string for the field
    """
    label_html = f'    <label for="{escape_attr(field.name)}">{escape_html(field.label)}</label>'

    if field.field_type == 'textarea':
        return label_html + '\n' + _generate_textarea(field)
    elif field.field_type == 'select':
        return label_html + '\n' + _generate_select(field)
    elif field.field_type == 'checkbox':
        return _generate_checkbox(field)
    else:
        return label_html + '\n' + _generate_input(field)


def _generate_input(field: FieldSpec) -> str:
    """Generate HTML for input elements."""
    attrs = {
        'type': field.field_type,
        'name': field.name,
        'id': field.name,
    }

    # Add common attributes from options
    for key in ['placeholder', 'min', 'max', 'step', 'accept', 'pattern']:
        if key in field.options:
            attrs[key] = field.options[key]

    if 'required' in field.options:
        attrs['required'] = None

    if 'multiple' in field.options and field.field_type == 'file':
        attrs['multiple'] = None

    return '    ' + build_tag('input', attrs, self_closing=True)


def _generate_textarea(field: FieldSpec) -> str:
    """Generate HTML for textarea elements."""
    attrs = {
        'name': field.name,
        'id': field.name,
    }

    for key in ['rows', 'cols', 'placeholder']:
        if key in field.options:
            attrs[key] = field.options[key]

    if 'required' in field.options:
        attrs['required'] = None

    return '    ' + build_tag('textarea', attrs) + '</textarea>'


def _generate_select(field: FieldSpec) -> str:
    """Generate HTML for select elements."""
    attrs = {
        'name': field.name,
        'id': field.name,
    }

    if 'required' in field.options:
        attrs['required'] = None

    if 'multiple' in field.options:
        attrs['multiple'] = None

    select_html = '    ' + build_tag('select', attrs) + '\n'

    # Parse options
    if 'options' in field.options:
        option_values = field.options['options'].split('|')
        for option in option_values:
            option = option.strip()
            select_html += f'        <option value="{escape_attr(option)}">{escape_html(option)}</option>\n'

    select_html += '    </select>'
    return select_html


def _generate_checkbox(field: FieldSpec) -> str:
    """Generate HTML for checkbox elements."""
    attrs = {
        'type': 'checkbox',
        'name': field.name,
        'id': field.name,
        'value': field.options.get('value', 'on'),
    }

    if 'required' in field.options:
        attrs['required'] = None

    checkbox_html = '    ' + build_tag('input', attrs, self_closing=True)
    label_html = f' <label for="{escape_attr(field.name)}">{escape_html(field.label)}</label>'

    return checkbox_html + label_html


def build_tag(tag: str, attrs: Dict[str, Optional[str]], self_closing: bool = False) -> str:
    """
    Build an HTML tag with attributes.

    Args:
        tag: Tag name
        attrs: Dictionary of attributes (value=None for boolean attributes)
        self_closing: Whether to make tag self-closing

    Returns:
        HTML tag string
    """
    attr_parts = []
    for key, value in attrs.items():
        if value is None:
            # Boolean attribute
            attr_parts.append(key)
        else:
            attr_parts.append(f'{key}="{escape_attr(value)}"')

    attr_str = ' ' + ' '.join(attr_parts) if attr_parts else ''
    if self_closing:
        return f'<{tag}{attr_str}>'
    else:
        return f'<{tag}{attr_str}>'


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


def escape_attr(text: str) -> str:
    """Escape HTML attribute values."""
    if not text:
        return ''
    return (text
            .replace('&', '&amp;')
            .replace('"', '&quot;')
            .replace('<', '&lt;')
            .replace('>', '&gt;'))


def dict_to_fieldspec(field_dict: Dict) -> FieldSpec:
    """
    Convert a field dictionary to a FieldSpec object.

    Args:
        field_dict: Dictionary with field properties (name, type, label, etc.)

    Returns:
        FieldSpec instance
    """
    name = field_dict['name']
    field_type = field_dict['type']
    label = field_dict.get('label')

    # Convert remaining properties to options dict (as strings)
    options = {}
    for key in ['placeholder', 'min', 'max', 'step', 'accept', 'pattern',
                'rows', 'cols', 'value']:
        if key in field_dict and field_dict[key] is not None:
            options[key] = str(field_dict[key])

    # Handle required (bool -> 'true' string presence)
    if field_dict.get('required'):
        options['required'] = 'true'

    # Handle multiple (bool -> 'true' string presence)
    if field_dict.get('multiple'):
        options['multiple'] = 'true'

    # Handle select options (list -> pipe-separated string)
    if 'options' in field_dict and field_dict['options']:
        opts = field_dict['options']
        if isinstance(opts, list):
            options['options'] = '|'.join(str(o) for o in opts)
        else:
            options['options'] = str(opts)

    return FieldSpec(name, field_type, label, options)


def generate_form_html_from_dicts(
    fields: List[Dict],
    title: Optional[str] = None,
    text: Optional[str] = None,
    add_submit: bool = True,
    add_cancel: bool = True,
    cancel_label: str = 'Cancel'
) -> str:
    """
    Generate complete HTML document from field dictionaries.

    This is the entry point for the Python API.

    Args:
        fields: List of field dictionaries
        title: Page title
        text: Instructional text above form
        add_submit: Whether to add submit button
        add_cancel: Whether to add cancel button
        cancel_label: Label text for the cancel button

    Returns:
        Complete HTML document string
    """
    # Convert dicts to FieldSpec objects
    field_specs = [dict_to_fieldspec(f) for f in fields]

    # Generate form HTML
    form_html = generate_form_html(field_specs, add_submit_button=add_submit,
                                   add_cancel_button=add_cancel, cancel_label=cancel_label)

    # Wrap in complete HTML document
    title_html = f'<h1>{escape_html(title)}</h1>' if title else ''
    text_html = f'<p>{escape_html(text)}</p>' if text else ''

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{escape_html(title) if title else 'Form'}</title>
    <style>
        body {{
            font-family: system-ui, -apple-system, sans-serif;
            max-width: 600px;
            margin: 40px auto;
            padding: 20px;
        }}
        label {{
            display: block;
            margin: 15px 0 5px;
            font-weight: 500;
        }}
        input, select, textarea {{
            width: 100%;
            padding: 8px;
            border: 1px solid #ccc;
            border-radius: 4px;
            box-sizing: border-box;
        }}
        input[type="checkbox"] {{
            width: auto;
            margin-right: 8px;
        }}
        button {{
            margin-top: 20px;
            padding: 10px 20px;
            background: #007bff;
            color: white;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            margin-right: 10px;
        }}
        button:hover {{
            background: #0056b3;
        }}
        button.cancel {{
            background: #6c757d;
        }}
        button.cancel:hover {{
            background: #5a6268;
        }}
    </style>
</head>
<body>
    {title_html}
    {text_html}
    {form_html}
</body>
</html>'''

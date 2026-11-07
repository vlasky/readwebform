"""
Declarative form generation for readwebform.
"""

import html as html_mod
import urllib.parse
from typing import Dict, List, Optional

from .parser import render_page

# Field types accepted by --field and the Python API
VALID_TYPES = frozenset({
    'text', 'email', 'password', 'number', 'date', 'url', 'tel', 'search',
    'time', 'datetime-local', 'month', 'week', 'range', 'color', 'hidden',
    'textarea', 'select', 'checkbox', 'radio', 'file',
})

# Options that take no value. Anything else without '=' continues the
# previous option's value, so accept=.pdf,.docx works as documented.
FLAG_OPTIONS = frozenset({'required', 'multiple', 'checked', 'readonly', 'disabled', 'autofocus'})

# Options copied verbatim onto <input> elements when present
_INPUT_PASSTHROUGH = ('placeholder', 'min', 'max', 'step', 'accept', 'pattern',
                      'minlength', 'maxlength', 'autocomplete', 'list')


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
        Parse a field specification string.

        Format: name:type[:label][:options]
        Example: name:text:Full+Name:required,placeholder=Your+name

        Labels and option values are URL-decoded (``+`` becomes a space,
        ``%2C`` a comma). Options are comma-separated ``key=value`` pairs or
        bare flags (``required``, ``multiple``, ``checked``...). A comma in a
        value is allowed where the text after it is not a flag, so
        ``accept=.pdf,.docx`` is a single option.

        Raises:
            ValueError: If the specification is invalid
        """
        parts = spec.split(':', 3)
        if len(parts) < 2:
            raise ValueError(f'Invalid field spec: {spec} (expected name:type[:label][:options])')

        name = parts[0].strip()
        field_type = parts[1].strip().lower()
        label = None
        options: Dict[str, str] = {}

        if not name:
            raise ValueError(f'Invalid field spec: {spec} (field name is empty)')

        if field_type not in VALID_TYPES:
            raise ValueError(
                f'Invalid field type: {field_type} (must be one of {", ".join(sorted(VALID_TYPES))})'
            )

        if len(parts) >= 3 and parts[2] != '':
            label = urllib.parse.unquote_plus(parts[2])

        if len(parts) >= 4:
            options = cls._parse_options(parts[3])

        return cls(name, field_type, label, options)

    @staticmethod
    def _parse_options(options_str: str) -> Dict[str, str]:
        """Parse an options string into a dictionary."""
        options: Dict[str, str] = {}
        last_key: Optional[str] = None
        for segment in options_str.split(','):
            segment = segment.strip()
            if not segment:
                continue

            if '=' in segment:
                key, value = segment.split('=', 1)
                key = key.strip().lower()
                options[key] = urllib.parse.unquote_plus(value.strip())
                last_key = key
            elif segment.lower() in FLAG_OPTIONS:
                options[segment.lower()] = 'true'
                last_key = None
            elif last_key is not None:
                # Continuation of a value that contains a comma
                options[last_key] += ',' + urllib.parse.unquote_plus(segment)
            else:
                raise ValueError(f'Unknown option: {segment}')

        return options

    def has_flag(self, name: str) -> bool:
        return name in self.options


def generate_form_html(fields: List[FieldSpec], add_submit_button: bool = True,
                       add_cancel_button: bool = True, cancel_label: str = 'Cancel') -> str:
    """
    Generate an HTML form from field specifications.

    Args:
        fields: List of FieldSpec instances
        add_submit_button: Whether to add a submit button
        add_cancel_button: Whether to add a cancel button
        cancel_label: Label text for the cancel button

    Returns:
        HTML form string
    """
    has_file_field = any(f.field_type == 'file' for f in fields)
    enctype = ' enctype="multipart/form-data"' if has_file_field else ''

    form_parts = [f'<form method="POST"{enctype}>']

    for field in fields:
        form_parts.append(generate_field_html(field))

    if add_submit_button:
        form_parts.append('    <button type="submit">Submit</button>')

    if add_cancel_button:
        form_parts.append('    <button type="submit" name="_cancel" value="1" class="cancel" '
                          f'formnovalidate>{escape_html(cancel_label)}</button>')

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
    if field.field_type == 'select':
        return label_html + '\n' + _generate_select(field)
    if field.field_type == 'checkbox':
        return _generate_checkbox(field)
    if field.field_type == 'radio':
        return _generate_radio_group(field)
    if field.field_type == 'hidden':
        return _generate_input(field)
    return label_html + '\n' + _generate_input(field)


def _common_attrs(field: FieldSpec) -> Dict[str, Optional[str]]:
    attrs: Dict[str, Optional[str]] = {}
    for flag in ('required', 'readonly', 'disabled', 'autofocus'):
        if field.has_flag(flag):
            attrs[flag] = None
    return attrs


def _generate_input(field: FieldSpec) -> str:
    """Generate HTML for <input> elements."""
    attrs: Dict[str, Optional[str]] = {
        'type': field.field_type,
        'name': field.name,
        'id': field.name,
    }

    for key in _INPUT_PASSTHROUGH:
        if key in field.options:
            attrs[key] = field.options[key]

    if 'value' in field.options and field.field_type != 'file':
        attrs['value'] = field.options['value']

    attrs.update(_common_attrs(field))

    if field.has_flag('multiple') and field.field_type in ('file', 'email'):
        attrs['multiple'] = None

    return '    ' + build_tag('input', attrs)


def _generate_textarea(field: FieldSpec) -> str:
    """Generate HTML for <textarea> elements."""
    attrs: Dict[str, Optional[str]] = {
        'name': field.name,
        'id': field.name,
    }

    for key in ('rows', 'cols', 'placeholder', 'minlength', 'maxlength'):
        if key in field.options:
            attrs[key] = field.options[key]

    attrs.update(_common_attrs(field))

    content = escape_html(field.options.get('value', ''))
    return '    ' + build_tag('textarea', attrs) + content + '</textarea>'


def _option_values(field: FieldSpec) -> List[str]:
    raw = field.options.get('options', '')
    return [o.strip() for o in raw.split('|')] if raw else []


def _selected_values(field: FieldSpec) -> List[str]:
    value = field.options.get('value')
    if value is None:
        return []
    if field.has_flag('multiple'):
        return [v.strip() for v in value.split('|')]
    return [value]


def _generate_select(field: FieldSpec) -> str:
    """Generate HTML for <select> elements."""
    attrs: Dict[str, Optional[str]] = {
        'name': field.name,
        'id': field.name,
    }
    attrs.update(_common_attrs(field))
    if field.has_flag('multiple'):
        attrs['multiple'] = None

    selected = _selected_values(field)
    lines = ['    ' + build_tag('select', attrs)]
    for option in _option_values(field):
        flag = ' selected' if option in selected else ''
        lines.append(f'        <option value="{escape_attr(option)}"{flag}>{escape_html(option)}</option>')
    lines.append('    </select>')
    return '\n'.join(lines)


def _generate_checkbox(field: FieldSpec) -> str:
    """Generate HTML for a checkbox with a trailing label."""
    attrs: Dict[str, Optional[str]] = {
        'type': 'checkbox',
        'name': field.name,
        'id': field.name,
        'value': field.options.get('value', 'on'),
    }
    if field.has_flag('checked'):
        attrs['checked'] = None
    attrs.update(_common_attrs(field))

    checkbox_html = '    ' + build_tag('input', attrs)
    label_html = f' <label for="{escape_attr(field.name)}">{escape_html(field.label)}</label>'
    return checkbox_html + label_html


def _generate_radio_group(field: FieldSpec) -> str:
    """Generate HTML for a group of radio buttons inside a fieldset."""
    selected = field.options.get('value')
    lines = ['    <fieldset>', f'        <legend>{escape_html(field.label)}</legend>']
    for i, option in enumerate(_option_values(field)):
        option_id = f'{field.name}_{i}'
        attrs: Dict[str, Optional[str]] = {
            'type': 'radio',
            'name': field.name,
            'id': option_id,
            'value': option,
        }
        if option == selected:
            attrs['checked'] = None
        if field.has_flag('required'):
            attrs['required'] = None
        if field.has_flag('disabled'):
            attrs['disabled'] = None
        lines.append('        ' + build_tag('input', attrs)
                     + f' <label for="{escape_attr(option_id)}">{escape_html(option)}</label>')
    lines.append('    </fieldset>')
    return '\n'.join(lines)


def build_tag(tag: str, attrs: Dict[str, Optional[str]]) -> str:
    """
    Build an HTML start tag with attributes.

    Args:
        tag: Tag name
        attrs: Dictionary of attributes (value=None for boolean attributes)

    Returns:
        HTML start tag string
    """
    attr_parts = []
    for key, value in attrs.items():
        if value is None:
            attr_parts.append(key)
        else:
            attr_parts.append(f'{key}="{escape_attr(value)}"')

    attr_str = ' ' + ' '.join(attr_parts) if attr_parts else ''
    return f'<{tag}{attr_str}>'


def escape_html(text: Optional[str]) -> str:
    """Escape HTML special characters in text content."""
    if not text:
        return ''
    return html_mod.escape(text, quote=True)


def escape_attr(text: Optional[str]) -> str:
    """Escape HTML special characters in an attribute value."""
    if not text:
        return ''
    return html_mod.escape(text, quote=True)


def dict_to_fieldspec(field_dict: Dict) -> FieldSpec:
    """
    Convert a field dictionary (as used by the Python API) to a FieldSpec.

    Args:
        field_dict: Dictionary with field properties (name, type, label, etc.)

    Returns:
        FieldSpec instance

    Raises:
        ValueError: If the name or type is missing or invalid
    """
    name = field_dict.get('name')
    field_type = str(field_dict.get('type', '')).lower()
    if not name:
        raise ValueError('Field is missing a name')
    if field_type not in VALID_TYPES:
        raise ValueError(f'Invalid field type: {field_type!r} for field {name!r} '
                         f'(must be one of {", ".join(sorted(VALID_TYPES))})')

    label = field_dict.get('label')

    options: Dict[str, str] = {}
    for key in ('placeholder', 'min', 'max', 'step', 'accept', 'pattern',
                'rows', 'cols', 'minlength', 'maxlength', 'autocomplete'):
        if field_dict.get(key) is not None:
            options[key] = str(field_dict[key])

    for flag in FLAG_OPTIONS:
        if field_dict.get(flag):
            options[flag] = 'true'

    value = field_dict.get('value')
    if value is not None:
        if isinstance(value, (list, tuple)):
            options['value'] = '|'.join(str(v) for v in value)
        else:
            options['value'] = str(value)

    opts = field_dict.get('options')
    if opts:
        if isinstance(opts, (list, tuple)):
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
    Generate a complete HTML document from field dictionaries.

    This is the entry point for the Python API.

    Args:
        fields: List of field dictionaries
        title: Page title
        text: Instructional text above the form
        add_submit: Whether to add a submit button
        add_cancel: Whether to add a cancel button
        cancel_label: Label text for the cancel button

    Returns:
        Complete HTML document string
    """
    field_specs = [dict_to_fieldspec(f) for f in fields]
    form_html = generate_form_html(field_specs, add_submit_button=add_submit,
                                   add_cancel_button=add_cancel, cancel_label=cancel_label)
    return render_page(form_html, title=title, text=text)

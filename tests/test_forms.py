"""
Tests for declarative form generation.
"""

import pytest
from readwebform.forms import (
    FieldSpec,
    generate_form_html,
    generate_field_html,
    escape_html,
    escape_attr
)


class TestFieldSpec:
    """Test field specification parsing."""

    def test_parse_basic(self):
        spec = FieldSpec.parse('name:text')
        assert spec.name == 'name'
        assert spec.field_type == 'text'
        assert spec.label == 'name'
        assert spec.options == {}

    def test_parse_with_label(self):
        spec = FieldSpec.parse('email:email:Email+Address')
        assert spec.name == 'email'
        assert spec.field_type == 'email'
        assert spec.label == 'Email Address'

    def test_parse_with_options(self):
        spec = FieldSpec.parse('age:number:Age:min=0,max=120,required')
        assert spec.name == 'age'
        assert spec.field_type == 'number'
        assert spec.label == 'Age'
        assert spec.options['min'] == '0'
        assert spec.options['max'] == '120'
        assert spec.options['required'] == 'true'

    def test_parse_url_encoding(self):
        spec = FieldSpec.parse('name:text:Full+Name:placeholder=Your+name')
        assert spec.label == 'Full Name'
        assert spec.options['placeholder'] == 'Your name'

    def test_parse_invalid_missing_type(self):
        with pytest.raises(ValueError, match='Invalid field spec'):
            FieldSpec.parse('name')

    def test_parse_invalid_type(self):
        with pytest.raises(ValueError, match='Invalid field type'):
            FieldSpec.parse('name:invalid')

    def test_valid_types(self):
        valid_types = ['text', 'email', 'password', 'number', 'date', 'url',
                      'textarea', 'select', 'checkbox', 'file']
        for field_type in valid_types:
            spec = FieldSpec.parse(f'test:{field_type}')
            assert spec.field_type == field_type


class TestGenerateFormHTML:
    """Test form HTML generation."""

    def test_generate_simple_form(self):
        fields = [FieldSpec('name', 'text')]
        html = generate_form_html(fields)
        assert '<form' in html
        assert 'name="name"' in html
        assert 'type="text"' in html
        assert '<button type="submit">Submit</button>' in html

    def test_generate_without_submit(self):
        fields = [FieldSpec('name', 'text')]
        html = generate_form_html(fields, add_submit_button=False)
        assert '<button type="submit">' not in html

    def test_generate_with_file_field(self):
        fields = [
            FieldSpec('name', 'text'),
            FieldSpec('upload', 'file')
        ]
        html = generate_form_html(fields)
        assert 'enctype="multipart/form-data"' in html

    def test_generate_multiple_fields(self):
        fields = [
            FieldSpec('name', 'text', 'Full Name'),
            FieldSpec('email', 'email', 'Email'),
            FieldSpec('age', 'number', 'Age')
        ]
        html = generate_form_html(fields)
        assert 'name="name"' in html
        assert 'name="email"' in html
        assert 'name="age"' in html


class TestGenerateFieldHTML:
    """Test individual field HTML generation."""

    def test_generate_text_input(self):
        field = FieldSpec('username', 'text', 'Username', {'required': 'true', 'placeholder': 'Enter username'})
        html = generate_field_html(field)
        assert '<label for="username">Username</label>' in html
        assert 'type="text"' in html
        assert 'name="username"' in html
        assert 'required' in html
        assert 'placeholder="Enter username"' in html

    def test_generate_textarea(self):
        field = FieldSpec('comments', 'textarea', 'Comments', {'rows': '5', 'cols': '40'})
        html = generate_field_html(field)
        assert '<textarea' in html
        assert 'name="comments"' in html
        assert 'rows="5"' in html
        assert 'cols="40"' in html

    def test_generate_select(self):
        field = FieldSpec('country', 'select', 'Country', {'options': 'US|UK|CA', 'required': 'true'})
        html = generate_field_html(field)
        assert '<select' in html
        assert 'name="country"' in html
        assert 'required' in html
        assert '<option value="US">US</option>' in html
        assert '<option value="UK">UK</option>' in html
        assert '<option value="CA">CA</option>' in html

    def test_generate_checkbox(self):
        field = FieldSpec('agree', 'checkbox', 'I agree to terms')
        html = generate_field_html(field)
        assert 'type="checkbox"' in html
        assert 'name="agree"' in html
        assert '<label for="agree">I agree to terms</label>' in html

    def test_generate_file_input(self):
        field = FieldSpec('upload', 'file', 'Upload File', {'accept': '.pdf,.docx', 'multiple': 'true'})
        html = generate_field_html(field)
        assert 'type="file"' in html
        assert 'name="upload"' in html
        assert 'accept=".pdf,.docx"' in html
        assert 'multiple' in html

    def test_generate_number_input(self):
        field = FieldSpec('age', 'number', 'Age', {'min': '0', 'max': '120', 'step': '1'})
        html = generate_field_html(field)
        assert 'type="number"' in html
        assert 'min="0"' in html
        assert 'max="120"' in html
        assert 'step="1"' in html


class TestEscaping:
    """Test HTML escaping functions."""

    def test_escape_html(self):
        assert escape_html('<script>') == '&lt;script&gt;'
        assert escape_html('A & B') == 'A &amp; B'
        assert escape_html('"quoted"') == '&quot;quoted&quot;'

    def test_escape_attr(self):
        assert escape_attr('value"with"quotes') == 'value&quot;with&quot;quotes'
        assert escape_attr('<tag>') == '&lt;tag&gt;'

    def test_escape_empty(self):
        assert escape_html('') == ''
        assert escape_attr('') == ''

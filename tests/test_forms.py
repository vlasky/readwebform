"""
Tests for declarative form generation.
"""

import pytest

from readwebform.forms import FieldSpec, escape_attr, escape_html, generate_field_html, generate_form_html


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


class TestDefaultsAndNewTypes:
    """Regression tests for value handling and additional field types."""

    def test_text_value_rendered(self):
        html = generate_field_html(FieldSpec.parse('x:text:Label:value=hello+world'))
        assert 'value="hello world"' in html

    def test_textarea_value_rendered_as_content(self):
        html = generate_field_html(FieldSpec.parse('x:textarea::value=Some+%3Ctext%3E'))
        assert '>Some &lt;text&gt;</textarea>' in html

    def test_select_value_marks_selected(self):
        html = generate_field_html(FieldSpec.parse('c:select:Country:options=US|UK|CA,value=UK'))
        assert '<option value="UK" selected>UK</option>' in html
        assert '<option value="US">US</option>' in html

    def test_multi_select_values(self):
        html = generate_field_html(FieldSpec.parse('c:select::options=a|b|c,multiple,value=a|c'))
        assert '<select name="c" id="c" multiple>' in html
        assert html.count(' selected') == 2

    def test_checkbox_checked_flag(self):
        html = generate_field_html(FieldSpec.parse('agree:checkbox:Agree:checked,value=yes'))
        assert 'type="checkbox"' in html and ' checked' in html and 'value="yes"' in html

    def test_radio_group(self):
        html = generate_field_html(FieldSpec.parse('plan:radio:Plan:options=free|pro,value=pro,required'))
        assert '<legend>Plan</legend>' in html
        assert '<input type="radio" name="plan" id="plan_0" value="free" required>' in html
        assert '<input type="radio" name="plan" id="plan_1" value="pro" checked required>' in html
        assert '<label for="plan_1">pro</label>' in html

    def test_hidden_field_has_no_label(self):
        html = generate_field_html(FieldSpec.parse('token:hidden::value=abc'))
        assert '<label' not in html
        assert '<input type="hidden" name="token" id="token" value="abc">' in html

    def test_new_input_types_accepted(self):
        for t in ['tel', 'search', 'time', 'datetime-local', 'month', 'week', 'range', 'color']:
            html = generate_field_html(FieldSpec.parse(f'f:{t}'))
            assert f'type="{t}"' in html

    def test_accept_with_literal_commas(self):
        spec = FieldSpec.parse('upload:file:Upload:accept=.pdf,.docx,.txt,required')
        assert spec.options == {'accept': '.pdf,.docx,.txt', 'required': 'true'}
        assert 'accept=".pdf,.docx,.txt"' in generate_field_html(spec)

    def test_pattern_with_comma(self):
        spec = FieldSpec.parse('code:text::pattern=[a-z]{2,4}')
        assert spec.options['pattern'] == '[a-z]{2,4}'

    def test_unknown_bare_option_rejected(self):
        with pytest.raises(ValueError, match='Unknown option'):
            FieldSpec.parse('x:text::bogus')

    def test_empty_name_rejected(self):
        with pytest.raises(ValueError, match='empty'):
            FieldSpec.parse(':text')

    def test_file_value_ignored(self):
        html = generate_field_html(FieldSpec.parse('f:file::value=secret'))
        assert 'value=' not in html


class TestDictToFieldSpec:
    def test_value_and_checked_from_dict(self):
        from readwebform.forms import dict_to_fieldspec
        spec = dict_to_fieldspec({'name': 'n', 'type': 'checkbox', 'checked': True, 'value': 'yes'})
        html = generate_field_html(spec)
        assert ' checked' in html and 'value="yes"' in html

    def test_list_value_for_multi_select(self):
        from readwebform.forms import dict_to_fieldspec
        spec = dict_to_fieldspec({'name': 'c', 'type': 'select', 'options': ['a', 'b'],
                                  'multiple': True, 'value': ['a', 'b']})
        assert generate_field_html(spec).count(' selected') == 2

    def test_invalid_type_rejected(self):
        from readwebform.forms import dict_to_fieldspec
        with pytest.raises(ValueError, match='Invalid field type'):
            dict_to_fieldspec({'name': 'n', 'type': 'bogus'})

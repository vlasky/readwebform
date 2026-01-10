"""
Tests for the Python API, including Field dataclass.
"""

import pytest

from readwebform import Field, FormResult, UploadedFile
from readwebform.api import read_webform_fields
from readwebform.forms import generate_form_html_from_dicts, dict_to_fieldspec


class TestFieldDataclass:
    """Test Field dataclass functionality."""

    def test_field_basic_creation(self):
        """Test basic Field creation with required parameters."""
        field = Field(name='email', type='email')
        assert field.name == 'email'
        assert field.type == 'email'
        assert field.label is None
        assert field.required is False

    def test_field_all_parameters(self):
        """Test Field creation with all parameters."""
        field = Field(
            name='age',
            type='number',
            label='Your Age',
            required=True,
            placeholder='Enter your age',
            min=0,
            max=120,
            step=1
        )
        assert field.name == 'age'
        assert field.type == 'number'
        assert field.label == 'Your Age'
        assert field.required is True
        assert field.placeholder == 'Enter your age'
        assert field.min == 0
        assert field.max == 120
        assert field.step == 1

    def test_field_textarea_options(self):
        """Test Field with textarea-specific options."""
        field = Field(
            name='bio',
            type='textarea',
            label='Biography',
            rows=5,
            cols=40,
            placeholder='Tell us about yourself'
        )
        assert field.rows == 5
        assert field.cols == 40
        assert field.placeholder == 'Tell us about yourself'

    def test_field_select_options(self):
        """Test Field with select options."""
        field = Field(
            name='country',
            type='select',
            label='Country',
            options=['US', 'UK', 'CA', 'AU'],
            required=True
        )
        assert field.options == ['US', 'UK', 'CA', 'AU']
        assert field.required is True

    def test_field_file_options(self):
        """Test Field with file input options."""
        field = Field(
            name='document',
            type='file',
            label='Upload Document',
            accept='.pdf,.doc,.docx',
            multiple=True
        )
        assert field.accept == '.pdf,.doc,.docx'
        assert field.multiple is True

    def test_field_pattern(self):
        """Test Field with regex pattern."""
        field = Field(
            name='zipcode',
            type='text',
            label='ZIP Code',
            pattern=r'^\d{5}(-\d{4})?$'
        )
        assert field.pattern == r'^\d{5}(-\d{4})?$'

    def test_field_value(self):
        """Test Field with default value."""
        field = Field(
            name='newsletter',
            type='checkbox',
            label='Subscribe to newsletter',
            value='yes'
        )
        assert field.value == 'yes'


class TestFieldToDict:
    """Test Field.to_dict() conversion."""

    def test_to_dict_minimal(self):
        """Test to_dict with minimal field."""
        field = Field(name='test', type='text')
        result = field.to_dict()
        assert result == {'name': 'test', 'type': 'text'}

    def test_to_dict_with_label(self):
        """Test to_dict includes label when set."""
        field = Field(name='test', type='text', label='Test Label')
        result = field.to_dict()
        assert result['label'] == 'Test Label'

    def test_to_dict_with_required(self):
        """Test to_dict includes required when True."""
        field = Field(name='test', type='text', required=True)
        result = field.to_dict()
        assert result['required'] is True

    def test_to_dict_excludes_false_required(self):
        """Test to_dict excludes required when False."""
        field = Field(name='test', type='text', required=False)
        result = field.to_dict()
        assert 'required' not in result

    def test_to_dict_with_all_options(self):
        """Test to_dict with all field options."""
        field = Field(
            name='age',
            type='number',
            label='Age',
            required=True,
            placeholder='Enter age',
            min=0,
            max=120,
            step=1
        )
        result = field.to_dict()
        assert result == {
            'name': 'age',
            'type': 'number',
            'label': 'Age',
            'required': True,
            'placeholder': 'Enter age',
            'min': 0,
            'max': 120,
            'step': 1
        }

    def test_to_dict_with_select_options(self):
        """Test to_dict preserves select options list."""
        field = Field(
            name='color',
            type='select',
            options=['red', 'green', 'blue']
        )
        result = field.to_dict()
        assert result['options'] == ['red', 'green', 'blue']

    def test_to_dict_with_file_options(self):
        """Test to_dict with file field options."""
        field = Field(
            name='upload',
            type='file',
            accept='.jpg,.png',
            multiple=True
        )
        result = field.to_dict()
        assert result['accept'] == '.jpg,.png'
        assert result['multiple'] is True

    def test_to_dict_excludes_none_values(self):
        """Test to_dict doesn't include None values."""
        field = Field(name='test', type='text')
        result = field.to_dict()
        # Should only have name and type
        assert len(result) == 2
        assert 'label' not in result
        assert 'placeholder' not in result
        assert 'min' not in result

    def test_to_dict_excludes_false_multiple(self):
        """Test to_dict excludes multiple when False."""
        field = Field(name='upload', type='file', multiple=False)
        result = field.to_dict()
        assert 'multiple' not in result


class TestFieldTypes:
    """Test all supported field types."""

    @pytest.mark.parametrize("field_type", [
        'text', 'email', 'password', 'number', 'date', 'url',
        'textarea', 'select', 'checkbox', 'file'
    ])
    def test_all_field_types_accepted(self, field_type):
        """Test that all valid field types can be created."""
        field = Field(name='test', type=field_type)
        assert field.type == field_type
        result = field.to_dict()
        assert result['type'] == field_type


class TestFieldEquality:
    """Test Field equality and hashing."""

    def test_fields_equal(self):
        """Test that identical fields are equal."""
        field1 = Field(name='email', type='email', required=True)
        field2 = Field(name='email', type='email', required=True)
        assert field1 == field2

    def test_fields_not_equal(self):
        """Test that different fields are not equal."""
        field1 = Field(name='email', type='email')
        field2 = Field(name='email', type='text')
        assert field1 != field2


class TestMixedFieldAndDict:
    """Test that Field and dict can be mixed in field lists."""

    def test_field_and_dict_both_converted(self):
        """Test that mixed lists are handled correctly."""
        # This tests the internal conversion in read_webform_fields
        from readwebform.api import Field

        fields = [
            Field(name='name', type='text', label='Name'),
            {'name': 'email', 'type': 'email', 'label': 'Email'}
        ]

        # Convert like read_webform_fields does
        field_dicts = []
        for f in fields:
            if isinstance(f, Field):
                field_dicts.append(f.to_dict())
            else:
                field_dicts.append(f)

        assert len(field_dicts) == 2
        assert field_dicts[0] == {'name': 'name', 'type': 'text', 'label': 'Name'}
        assert field_dicts[1] == {'name': 'email', 'type': 'email', 'label': 'Email'}


class TestUploadedFile:
    """Test UploadedFile dataclass."""

    def test_uploaded_file_creation(self):
        """Test basic UploadedFile creation."""
        uploaded = UploadedFile(
            filename='test.pdf',
            path='/tmp/test.pdf',
            size=1024,
            content_type='application/pdf'
        )
        assert uploaded.filename == 'test.pdf'
        assert uploaded.path == '/tmp/test.pdf'
        assert uploaded.size == 1024
        assert uploaded.content_type == 'application/pdf'

    def test_uploaded_file_equality(self):
        """Test UploadedFile equality."""
        file1 = UploadedFile(
            filename='test.pdf',
            path='/tmp/test.pdf',
            size=1024,
            content_type='application/pdf'
        )
        file2 = UploadedFile(
            filename='test.pdf',
            path='/tmp/test.pdf',
            size=1024,
            content_type='application/pdf'
        )
        assert file1 == file2

    def test_uploaded_file_inequality(self):
        """Test UploadedFile inequality."""
        file1 = UploadedFile(
            filename='test.pdf',
            path='/tmp/test.pdf',
            size=1024,
            content_type='application/pdf'
        )
        file2 = UploadedFile(
            filename='other.pdf',
            path='/tmp/test.pdf',
            size=1024,
            content_type='application/pdf'
        )
        assert file1 != file2

    def test_uploaded_file_immutable(self):
        """Test UploadedFile is frozen (immutable)."""
        uploaded = UploadedFile(
            filename='test.pdf',
            path='/tmp/test.pdf',
            size=1024,
            content_type='application/pdf'
        )
        with pytest.raises(AttributeError):
            uploaded.filename = 'changed.pdf'

    def test_uploaded_file_size_types(self):
        """Test UploadedFile size can be large."""
        # Test with a large file size (10GB)
        uploaded = UploadedFile(
            filename='large.bin',
            path='/tmp/large.bin',
            size=10737418240,  # 10 GB
            content_type='application/octet-stream'
        )
        assert uploaded.size == 10737418240


class TestFormResult:
    """Test FormResult dataclass."""

    def test_formresult_success(self):
        """Test successful FormResult."""
        result = FormResult(
            success=True,
            fields={'email': 'test@example.com'},
            files=None,
            error=None
        )
        assert result.success is True
        assert result.fields['email'] == 'test@example.com'
        assert result.files is None
        assert result.error is None

    def test_formresult_failure(self):
        """Test failed FormResult (timeout)."""
        result = FormResult(success=False, error='timeout')
        assert result.success is False
        assert result.fields is None
        assert result.files is None
        assert result.error == 'timeout'

    def test_formresult_error_message(self):
        """Test FormResult with custom error message."""
        result = FormResult(success=False, error='user cancelled')
        assert result.success is False
        assert result.error == 'user cancelled'

    def test_formresult_success_no_error(self):
        """Test successful FormResult has no error."""
        result = FormResult(success=True, fields={})
        assert result.success is True
        assert result.error is None

    def test_formresult_with_uploaded_files(self):
        """Test FormResult with UploadedFile objects."""
        result = FormResult(
            success=True,
            fields={'name': 'Test'},
            files={
                'document': UploadedFile(
                    filename='test.pdf',
                    path='/tmp/test.pdf',
                    size=2048,
                    content_type='application/pdf'
                )
            }
        )
        assert result.files['document'].filename == 'test.pdf'
        assert result.files['document'].path == '/tmp/test.pdf'
        assert result.files['document'].size == 2048
        assert result.files['document'].content_type == 'application/pdf'

    def test_formresult_multiple_files(self):
        """Test FormResult with multiple uploaded files."""
        result = FormResult(
            success=True,
            fields={},
            files={
                'photo': UploadedFile(
                    filename='photo.jpg',
                    path='/tmp/photo.jpg',
                    size=102400,
                    content_type='image/jpeg'
                ),
                'resume': UploadedFile(
                    filename='resume.pdf',
                    path='/tmp/resume.pdf',
                    size=51200,
                    content_type='application/pdf'
                )
            }
        )
        assert len(result.files) == 2
        assert result.files['photo'].filename == 'photo.jpg'
        assert result.files['photo'].content_type == 'image/jpeg'
        assert result.files['resume'].filename == 'resume.pdf'
        assert result.files['resume'].content_type == 'application/pdf'


class TestDictToFieldSpec:
    """Test dict_to_fieldspec conversion."""

    def test_basic_dict_conversion(self):
        """Test converting a basic dict to FieldSpec."""
        field_dict = {'name': 'email', 'type': 'email'}
        spec = dict_to_fieldspec(field_dict)
        assert spec.name == 'email'
        assert spec.field_type == 'email'
        assert spec.label == 'email'  # Defaults to name

    def test_dict_with_label(self):
        """Test dict conversion preserves label."""
        field_dict = {'name': 'email', 'type': 'email', 'label': 'Email Address'}
        spec = dict_to_fieldspec(field_dict)
        assert spec.label == 'Email Address'

    def test_dict_with_required(self):
        """Test required bool is converted to options string."""
        field_dict = {'name': 'email', 'type': 'email', 'required': True}
        spec = dict_to_fieldspec(field_dict)
        assert 'required' in spec.options
        assert spec.options['required'] == 'true'

    def test_dict_without_required(self):
        """Test required=False doesn't add to options."""
        field_dict = {'name': 'email', 'type': 'email', 'required': False}
        spec = dict_to_fieldspec(field_dict)
        assert 'required' not in spec.options

    def test_dict_with_numeric_options(self):
        """Test numeric values are converted to strings."""
        field_dict = {'name': 'age', 'type': 'number', 'min': 0, 'max': 120, 'step': 1}
        spec = dict_to_fieldspec(field_dict)
        assert spec.options['min'] == '0'
        assert spec.options['max'] == '120'
        assert spec.options['step'] == '1'

    def test_dict_with_select_options_list(self):
        """Test select options list is converted to pipe-separated string."""
        field_dict = {'name': 'country', 'type': 'select', 'options': ['US', 'UK', 'CA']}
        spec = dict_to_fieldspec(field_dict)
        assert spec.options['options'] == 'US|UK|CA'

    def test_dict_with_multiple(self):
        """Test multiple bool is converted."""
        field_dict = {'name': 'files', 'type': 'file', 'multiple': True}
        spec = dict_to_fieldspec(field_dict)
        assert spec.options['multiple'] == 'true'


class TestGenerateFormHtmlFromDicts:
    """Test generate_form_html_from_dicts function."""

    def test_basic_form_generation(self):
        """Test basic form generation from dict."""
        fields = [{'name': 'email', 'type': 'email'}]
        html = generate_form_html_from_dicts(fields)
        assert '<form' in html
        assert 'type="email"' in html
        assert 'name="email"' in html
        assert '<button type="submit">Submit</button>' in html

    def test_form_with_title(self):
        """Test form generation with title."""
        fields = [{'name': 'test', 'type': 'text'}]
        html = generate_form_html_from_dicts(fields, title='My Form')
        assert '<title>My Form</title>' in html
        assert '<h1>My Form</h1>' in html

    def test_form_with_text(self):
        """Test form generation with instructional text."""
        fields = [{'name': 'test', 'type': 'text'}]
        html = generate_form_html_from_dicts(fields, text='Please fill out')
        assert '<p>Please fill out</p>' in html

    def test_form_no_submit_button(self):
        """Test form generation without submit button."""
        fields = [{'name': 'test', 'type': 'text'}]
        html = generate_form_html_from_dicts(fields, add_submit=False)
        assert '<button type="submit">' not in html

    def test_form_with_required_field(self):
        """Test required fields have required attribute."""
        fields = [{'name': 'email', 'type': 'email', 'required': True}]
        html = generate_form_html_from_dicts(fields)
        assert 'required' in html

    def test_form_with_select(self):
        """Test select field generation."""
        fields = [{'name': 'country', 'type': 'select', 'options': ['US', 'UK']}]
        html = generate_form_html_from_dicts(fields)
        assert '<select' in html
        assert '<option value="US">US</option>' in html
        assert '<option value="UK">UK</option>' in html

    def test_form_with_textarea(self):
        """Test textarea field generation."""
        fields = [{'name': 'bio', 'type': 'textarea', 'rows': 5}]
        html = generate_form_html_from_dicts(fields)
        assert '<textarea' in html
        assert 'rows="5"' in html

    def test_form_with_file_input(self):
        """Test file input has multipart enctype."""
        fields = [{'name': 'doc', 'type': 'file'}]
        html = generate_form_html_from_dicts(fields)
        assert 'enctype="multipart/form-data"' in html
        assert 'type="file"' in html

    def test_form_escapes_title(self):
        """Test title is HTML escaped."""
        fields = [{'name': 'test', 'type': 'text'}]
        html = generate_form_html_from_dicts(fields, title='Test <script>alert(1)</script>')
        assert '&lt;script&gt;' in html
        assert '<script>' not in html


class TestFieldIntegration:
    """Test Field dataclass integration with form generation."""

    def test_field_generates_valid_html(self):
        """Test Field objects generate valid HTML."""
        fields = [
            Field(name='email', type='email', label='Email', required=True),
            Field(name='age', type='number', label='Age', min=0, max=120)
        ]
        field_dicts = [f.to_dict() for f in fields]
        html = generate_form_html_from_dicts(field_dicts, title='Test')
        assert '<form' in html
        assert 'type="email"' in html
        assert 'type="number"' in html
        assert 'min="0"' in html
        assert 'max="120"' in html
        assert 'required' in html

    def test_field_with_select_options(self):
        """Test Field with select options generates correct HTML."""
        fields = [Field(name='country', type='select', options=['US', 'UK', 'CA'])]
        field_dicts = [f.to_dict() for f in fields]
        html = generate_form_html_from_dicts(field_dicts)
        assert '<select' in html
        assert '<option value="US">US</option>' in html
        assert '<option value="UK">UK</option>' in html
        assert '<option value="CA">CA</option>' in html

    def test_mixed_field_and_dict(self):
        """Test mixed Field and dict list generates valid HTML."""
        fields = [
            Field(name='name', type='text', label='Name'),
            {'name': 'email', 'type': 'email', 'label': 'Email'}
        ]
        # Simulate what read_webform_fields does
        field_dicts = []
        for f in fields:
            if isinstance(f, Field):
                field_dicts.append(f.to_dict())
            else:
                field_dicts.append(f)

        html = generate_form_html_from_dicts(field_dicts)
        assert 'name="name"' in html
        assert 'name="email"' in html
        assert 'type="text"' in html
        assert 'type="email"' in html

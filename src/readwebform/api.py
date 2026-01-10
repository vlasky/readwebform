"""
High-level Python API for readwebform.
"""

from typing import Optional, Dict, List, Any, Union, Literal
from dataclasses import dataclass, field, asdict

from .server import FormServer
from .parser import wrap_html_fragment, inject_csrf_token, validate_html
from .forms import generate_form_html_from_dicts
from .browser import launch_browser


# Valid field types
FieldType = Literal[
    'text', 'email', 'password', 'number', 'date', 'url',
    'textarea', 'select', 'checkbox', 'file'
]


@dataclass
class Field:
    """
    Type-safe field specification for declarative form building.

    Provides IDE autocompletion and type checking for field definitions.

    Example:
        fields = [
            Field(name='email', type='email', label='Email Address', required=True),
            Field(name='age', type='number', label='Age', min=0, max=120),
            Field(name='country', type='select', label='Country', options=['US', 'UK', 'CA']),
        ]
        result = read_webform_fields(fields)
    """
    name: str
    type: FieldType
    label: Optional[str] = None
    required: bool = False
    placeholder: Optional[str] = None
    min: Optional[Union[int, float, str]] = None
    max: Optional[Union[int, float, str]] = None
    step: Optional[Union[int, float]] = None
    rows: Optional[int] = None
    cols: Optional[int] = None
    options: Optional[List[str]] = None
    accept: Optional[str] = None
    multiple: bool = False
    pattern: Optional[str] = None
    value: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert Field to dictionary format for internal use."""
        result = {'name': self.name, 'type': self.type}

        if self.label is not None:
            result['label'] = self.label
        if self.required:
            result['required'] = True
        if self.placeholder is not None:
            result['placeholder'] = self.placeholder
        if self.min is not None:
            result['min'] = self.min
        if self.max is not None:
            result['max'] = self.max
        if self.step is not None:
            result['step'] = self.step
        if self.rows is not None:
            result['rows'] = self.rows
        if self.cols is not None:
            result['cols'] = self.cols
        if self.options is not None:
            result['options'] = self.options
        if self.accept is not None:
            result['accept'] = self.accept
        if self.multiple:
            result['multiple'] = True
        if self.pattern is not None:
            result['pattern'] = self.pattern
        if self.value is not None:
            result['value'] = self.value

        return result


@dataclass(frozen=True)
class UploadedFile:
    """
    Metadata for an uploaded file.

    Provides type-safe access to file upload information.
    This dataclass is immutable (frozen).

    Attributes:
        filename: Original filename from the upload
        path: Local filesystem path where the file was saved
        size: Size of the uploaded file in bytes
        content_type: MIME type of the file (e.g., 'application/pdf')

    Example:
        if result.success and 'document' in result.files:
            doc = result.files['document']
            print(f"Saved {doc.filename} ({doc.size} bytes) to {doc.path}")
            print(f"Content type: {doc.content_type}")
            with open(doc.path, 'rb') as f:
                content = f.read()
    """
    filename: str
    path: str
    size: int
    content_type: str


@dataclass
class FormResult:
    """
    Result of form submission.

    Attributes:
        success: True if form was submitted successfully, False on timeout/error
        fields: Dictionary of field name -> value (None if not success)
        files: Dictionary of file field name -> UploadedFile (None if no files)
        error: Error description if success is False (e.g., 'timeout', 'cancelled')

    Example:
        result = read_webform(html, timeout=60)
        if result.success:
            print(f"Got email: {result.fields['email']}")
        else:
            print(f"Form failed: {result.error}")
    """
    success: bool
    fields: Optional[Dict[str, str]] = None
    files: Optional[Dict[str, UploadedFile]] = None
    error: Optional[str] = None


def read_webform(
    html: str,
    timeout: int = 300,
    launch_browser: bool = False,
    browser_path: Optional[str] = None,
    max_file_size: Optional[int] = None,
    max_total_size: Optional[int] = None,
    reset_timeout_on_error: bool = True,
    host: str = '127.0.0.1',
    port: Optional[int] = None
) -> FormResult:
    """
    Display a web form and wait for user submission.

    Args:
        html: HTML form content (fragment or complete document)
        timeout: Maximum seconds to wait for submission (default: 300)
        launch_browser: Whether to automatically open browser (default: False)
        browser_path: Path to specific browser executable (None = system default)
        max_file_size: Maximum individual file upload size in bytes
        max_total_size: Maximum total upload size in bytes
        reset_timeout_on_error: Whether to reset timeout on errors (default: True)
        host: Host to bind to (default: '127.0.0.1')
        port: Port to bind to (None = auto-select)

    Returns:
        FormResult with success flag, fields dict, and files dict

    Example:
        result = read_webform('<form><input name="email" type="email"><button>Submit</button></form>',
                              launch_browser=True)
        if result.success:
            print(f"Email: {result.fields['email']}")
    """
    # Validate HTML has exactly one form
    valid, error_msg = validate_html(html)
    if not valid:
        return FormResult(success=False, error=f'invalid_html: {error_msg}')

    # Wrap if fragment
    html = wrap_html_fragment(html)

    # Create server
    server = FormServer(
        html=html,
        host=host,
        port=port,
        max_file_size=max_file_size,
        max_total_size=max_total_size,
        timeout=timeout,
        reset_timeout_on_error=reset_timeout_on_error
    )

    # Inject CSRF token
    html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
    server.html = html_with_csrf

    # Serve form and optionally launch browser
    # Browser is launched inside serve() AFTER server is bound (fixes race condition)
    try:
        if launch_browser:
            # Empty string for browser_path means system default
            launch_path = browser_path if browser_path else ''
            success, form_data, file_metadata = server.serve(launch_browser_path=launch_path)
        else:
            success, form_data, file_metadata = server.serve()
    except OSError as e:
        return FormResult(success=False, error=f'bind_error: {e}')
    except Exception as e:
        return FormResult(success=False, error=f'server_error: {e}')

    # Convert to result object
    if success:
        # Convert file metadata dicts to UploadedFile objects
        uploaded_files = {}
        if file_metadata:
            for name, meta in file_metadata.items():
                uploaded_files[name] = UploadedFile(
                    filename=meta['filename'],
                    path=meta['path'],
                    size=meta['size'],
                    content_type=meta['content_type']
                )

        return FormResult(
            success=True,
            fields=form_data.fields if form_data else {},
            files=uploaded_files if uploaded_files else None,
            error=None
        )
    else:
        return FormResult(success=False, error='timeout')


def read_webform_fields(
    fields: List[Union[Field, Dict[str, Any]]],
    title: Optional[str] = None,
    text: Optional[str] = None,
    timeout: int = 300,
    launch_browser: bool = False,
    browser_path: Optional[str] = None,
    **kwargs
) -> FormResult:
    """
    Display a form built from field specifications and wait for user submission.

    Args:
        fields: List of field specifications. Each can be either:
            - A Field dataclass instance (recommended, provides IDE support)
            - A dict with keys: name, type, label, required, placeholder, min, max, step, rows, cols, options, accept
        title: Page title (optional)
        text: Instructional text above form (optional)
        timeout: Maximum seconds to wait (default: 300)
        launch_browser: Whether to open browser (default: False)
        browser_path: Path to specific browser (optional)
        **kwargs: Additional arguments passed to read_webform()

    Returns:
        FormResult with success flag, fields dict, and files dict

    Example using Field dataclass:
        result = read_webform_fields([
            Field(name='email', type='email', label='Email', required=True),
            Field(name='age', type='number', label='Age', min=0, max=120)
        ], launch_browser=True)

    Example using dicts (backward compatible):
        result = read_webform_fields([
            {'name': 'email', 'type': 'email', 'label': 'Email', 'required': True},
            {'name': 'age', 'type': 'number', 'label': 'Age', 'min': 0, 'max': 120}
        ], launch_browser=True)
    """
    # Convert Field objects to dicts for internal processing
    field_dicts = []
    for f in fields:
        if isinstance(f, Field):
            field_dicts.append(f.to_dict())
        else:
            field_dicts.append(f)

    # Generate HTML from fields
    html = generate_form_html_from_dicts(
        field_dicts,
        title=title,
        text=text,
        add_submit=True
    )

    # Use read_webform
    return read_webform(
        html=html,
        timeout=timeout,
        launch_browser=launch_browser,
        browser_path=browser_path,
        **kwargs
    )

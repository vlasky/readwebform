"""
High-level Python API for readwebform.
"""

from dataclasses import dataclass
from typing import Any, Literal, Optional, Union

from .forms import generate_form_html_from_dicts
from .parser import inject_csrf_token, validate_html, wrap_html_fragment
from .server import FormServer

# Valid field types
FieldType = Literal[
    'text', 'email', 'password', 'number', 'date', 'url', 'tel', 'search',
    'time', 'datetime-local', 'month', 'week', 'range', 'color', 'hidden',
    'textarea', 'select', 'checkbox', 'radio', 'file'
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
            Field(name='country', type='select', label='Country', options=['US', 'UK', 'CA'], value='UK'),
            Field(name='plan', type='radio', label='Plan', options=['free', 'pro']),
            Field(name='notify', type='checkbox', label='Email me', checked=True),
        ]

    ``value`` pre-fills the field: the text of an input or textarea, the
    selected option(s) of a select or radio group, or the submitted value of
    a checkbox (use ``checked`` to tick it).
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
    options: Optional[list[str]] = None
    accept: Optional[str] = None
    multiple: bool = False
    pattern: Optional[str] = None
    value: Optional[Union[str, int, float, list[str]]] = None
    checked: bool = False
    minlength: Optional[int] = None
    maxlength: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert Field to dictionary format for internal use."""
        result: dict[str, Any] = {'name': self.name, 'type': self.type}

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
        if self.checked:
            result['checked'] = True
        if self.minlength is not None:
            result['minlength'] = self.minlength
        if self.maxlength is not None:
            result['maxlength'] = self.maxlength

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
        fields: Dictionary of field name -> value. Repeated fields (checkbox
            groups, multi-selects) are lists of strings. None if not success.
        files: Dictionary of file field name -> UploadedFile, or a list of
            UploadedFile for ``<input type="file" multiple>``. None if no files.
        error: Error description if success is False (e.g., 'timeout', 'cancelled')

    Example:
        result = read_webform(html, timeout=60)
        if result.success:
            print(f"Got email: {result.fields['email']}")
        else:
            print(f"Form failed: {result.error}")
    """
    success: bool
    fields: Optional[dict[str, Union[str, list[str]]]] = None
    files: Optional[dict[str, Union[UploadedFile, list[UploadedFile]]]] = None
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
    port: Optional[int] = None,
    cert_file: Optional[str] = None,
    key_file: Optional[str] = None,
    url_file: Optional[str] = None,
    upload_dir: Optional[str] = None,
    url_host: Optional[str] = None
) -> FormResult:
    """
    Display a web form and wait for user submission.

    Args:
        html: HTML form content (fragment or complete document)
        timeout: Maximum seconds to wait for submission (default: 300)
        launch_browser: Whether to open the browser (default: False). Unlike the
            CLI, the API never opens a browser unless this is True.
        browser_path: Path to specific browser executable (None = system default)
        max_file_size: Maximum individual file upload size in bytes
        max_total_size: Maximum total upload size in bytes
        reset_timeout_on_error: Whether to reset timeout on errors (default: True)
        host: IPv4 or IPv6 address to bind to (default: '127.0.0.1')
        port: Port to bind to (None = auto-select)
        cert_file: Path to a PEM certificate to serve HTTPS (requires key_file)
        key_file: Path to the PEM private key for cert_file
        url_file: If given, the form URL is written to this file once listening
        upload_dir: Directory for uploaded files (default: a temporary directory
            created on first upload and removed if left empty)
        url_host: Host to put in the form URL (default: the bind host, or a
            best-effort guess of this machine's address for a wildcard bind)

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
        reset_timeout_on_error=reset_timeout_on_error,
        cert_file=cert_file,
        key_file=key_file,
        upload_dir=upload_dir,
        url_host=url_host
    )

    # Inject CSRF token
    html_with_csrf = inject_csrf_token(html, server.csrf_token, server.endpoint)
    server.html = html_with_csrf

    # Serve form and optionally launch browser
    # Browser is launched inside serve() AFTER server is bound (fixes race condition)
    try:
        # Empty string for browser_path means system default
        launch_path = (browser_path or '') if launch_browser else None
        success, form_data, file_metadata, cancelled = server.serve(
            launch_browser_path=launch_path, url_file=url_file)
    except OSError as e:
        return FormResult(success=False, error=f'bind_error: {e}')
    except Exception as e:
        return FormResult(success=False, error=f'server_error: {e}')

    # Handle cancellation
    if cancelled:
        return FormResult(success=False, error='cancelled')

    # Convert to result object
    if success:
        # Convert file metadata dicts to UploadedFile objects
        uploaded_files: dict[str, Union[UploadedFile, list[UploadedFile]]] = {}
        for name, meta in (file_metadata or {}).items():
            if isinstance(meta, list):
                uploaded_files[name] = [UploadedFile(**m) for m in meta]
            else:
                uploaded_files[name] = UploadedFile(**meta)

        return FormResult(
            success=True,
            fields=form_data.fields if form_data else {},
            files=uploaded_files if uploaded_files else None,
            error=None
        )
    else:
        return FormResult(success=False, error='timeout')


def read_webform_fields(
    fields: list[Union[Field, dict[str, Any]]],
    title: Optional[str] = None,
    text: Optional[str] = None,
    timeout: int = 300,
    launch_browser: bool = False,
    browser_path: Optional[str] = None,
    add_cancel_button: bool = True,
    cancel_label: str = 'Cancel',
    submit_label: str = 'Submit',
    **kwargs
) -> FormResult:
    """
    Display a form built from field specifications and wait for user submission.

    Args:
        fields: List of field specifications. Each can be either:
            - A Field dataclass instance (recommended, provides IDE support)
            - A dict with the same keys as Field (name, type, label, required,
              placeholder, value, checked, min, max, step, rows, cols, options, ...)
        title: Page title (optional)
        text: Instructional text above form (optional)
        timeout: Maximum seconds to wait (default: 300)
        launch_browser: Whether to open browser (default: False)
        browser_path: Path to specific browser (optional)
        add_cancel_button: Whether to add cancel button (default: True)
        cancel_label: Label for the cancel button (default: 'Cancel')
        submit_label: Label for the submit button (default: 'Submit')
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
        add_submit=True,
        add_cancel=add_cancel_button,
        cancel_label=cancel_label,
        submit_label=submit_label
    )

    # Use read_webform
    return read_webform(
        html=html,
        timeout=timeout,
        launch_browser=launch_browser,
        browser_path=browser_path,
        **kwargs
    )

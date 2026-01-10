"""
readwebform - A command-line utility for gathering structured user input through temporary web forms.

Copyright (c) 2025 Vlad Lasky (github@vladlasky.com)
Licensed under the MIT License.

Programmatic Usage:

    from readwebform import read_webform

    # Simple HTML form - URL printed to stderr, open browser manually
    result = read_webform('<form><input name="email" type="email"><button>Submit</button></form>')
    if result.success:
        print(f"Email: {result.fields['email']}")

    # Or with auto-launch browser
    result = read_webform(html, launch_browser=True)

    # Declarative with Field dataclass (recommended - provides IDE autocompletion)
    from readwebform import read_webform_fields, Field

    result = read_webform_fields([
        Field(name='email', type='email', label='Email', required=True),
        Field(name='age', type='number', label='Age', min=0, max=120)
    ], launch_browser=True)
    if result.success:
        print(f"Email: {result.fields['email']}, Age: {result.fields['age']}")

    # Dicts also work (backward compatible)
    result = read_webform_fields([
        {'name': 'email', 'type': 'email', 'label': 'Email', 'required': True},
        {'name': 'age', 'type': 'number', 'label': 'Age'}
    ])
"""

__version__ = "0.1.0"
__author__ = "Vlad Lasky"
__email__ = "github@vladlasky.com"

# High-level API (recommended)
from readwebform.api import read_webform, read_webform_fields, FormResult, Field, UploadedFile

# Low-level API (for advanced use)
from readwebform.server import FormServer

__all__ = [
    # High-level API
    'read_webform',
    'read_webform_fields',
    'FormResult',
    'Field',
    'UploadedFile',
    # Low-level API
    'FormServer',
]

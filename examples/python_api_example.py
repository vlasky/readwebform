#!/usr/bin/env python3
"""
Example: Using readwebform as a Python library
"""

from readwebform import read_webform, read_webform_fields


# Example 1: Simple email collection
def collect_email():
    """Collect an email address from the user via web form."""
    html = '<form><input name="email" type="email" required><button>Submit</button></form>'

    result = read_webform(html, timeout=60, launch_browser=True)

    if result.success:
        email = result.fields.get('email')
        print(f"✓ Email collected: {email}")
        return email
    else:
        print("✗ Timeout or error")
        return None


# Example 2: Declarative form generation
def collect_user_info():
    """Collect user information using declarative form generation."""
    fields = [
        {'name': 'name', 'type': 'text', 'label': 'Full Name', 'required': True},
        {'name': 'email', 'type': 'email', 'label': 'Email', 'required': True},
        {'name': 'age', 'type': 'number', 'label': 'Age', 'min': 0, 'max': 120},
        {'name': 'bio', 'type': 'textarea', 'label': 'Bio', 'rows': 4},
    ]

    result = read_webform_fields(
        fields,
        title="User Information",
        timeout=120,
        launch_browser=True
    )

    if result.success:
        print("\n✓ User information collected:")
        for key, value in result.fields.items():
            print(f"  {key}: {value}")
        return result.fields
    else:
        print("\n✗ Timeout or error")
        return None


# Example 3: File upload
def collect_file():
    """Collect a file upload from user."""
    html = '''
    <form enctype="multipart/form-data">
        <label>Upload your document:</label>
        <input type="file" name="document" accept=".pdf,.txt,.doc,.docx" required>
        <button type="submit">Upload</button>
    </form>
    '''

    result = read_webform(
        html,
        timeout=120,
        max_file_size=10*1024*1024,  # 10 MB limit
        launch_browser=True
    )

    if result.success and 'document' in result.files:
        file_info = result.files['document']
        print(f"\n✓ File uploaded:")
        print(f"  Filename: {file_info['filename']}")
        print(f"  Saved to: {file_info['path']}")
        return file_info['path']
    else:
        print("\n✗ No file uploaded or timeout")
        return None


if __name__ == '__main__':
    import sys

    print("readwebform Python API Examples")
    print("=" * 50)

    if len(sys.argv) > 1:
        example = sys.argv[1]
        if example == 'email':
            collect_email()
        elif example == 'user-info':
            collect_user_info()
        elif example == 'file':
            collect_file()
        else:
            print(f"Unknown example: {example}")
            print("Usage: python python_api_example.py [email|user-info|file]")
    else:
        print("\nUsage: python python_api_example.py [email|user-info|file]")
        print("\nExamples:")
        print("  python python_api_example.py email      - Collect email address")
        print("  python python_api_example.py user-info  - Collect user information")
        print("  python python_api_example.py file       - Upload a file")

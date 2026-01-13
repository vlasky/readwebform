# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2025-01-13

### Added

- Cancel button for forms - allows users to explicitly abort form submission
  - Enabled by default in declarative forms
  - `--no-cancel-button` flag to disable
  - `--cancel-label <text>` to customize button text
  - Returns `{"success": false, "error": "cancelled"}` on cancellation
  - Exit code 7 for cancelled forms
- Python API: `add_cancel_button` and `cancel_label` parameters for `read_webform_fields()`
- Custom HTML forms can add cancel button with `<button type="submit" name="_cancel" value="1">Cancel</button>`

### Changed

- Exit code 7 is now used for user cancellation (was previously invalid argument)
- Exit code 8 is now used for invalid argument

## [0.1.0] - 2025-01-10

### Added

- Initial release
- Serve custom HTML forms or generate from declarative field specifications
- JSON and environment variable output formats
- File upload support with configurable size limits
- CSRF protection and timeout management
- HTTPS support with user-provided certificates
- Python API (`read_webform`, `read_webform_fields`)
- Cross-platform browser launching
- Comprehensive test suite (150 tests)

[0.2.0]: https://github.com/vlasky/readwebform/releases/tag/v0.2.0
[0.1.0]: https://github.com/vlasky/readwebform/releases/tag/v0.1.0

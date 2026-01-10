# `readwebform` Design Document

## Overview

`readwebform` is a cross-platform command-line utility that allows scripts to gather structured user input through a temporary, locally served web form.  
It is conceptually similar to `readline`, but instead of prompting in the terminal, it uses a web browser to collect input interactively.

When invoked, `readwebform` launches a lightweight web server on `localhost`, serves a single-use form, and outputs the submitted data to the calling script in a structured, machine-readable format.

* * *

## Goals

-   Provide a **one-shot, ephemeral** browser-based prompt for scripts and automation.
    
-   Support **multiple input methods**: inline HTML, HTML file, stdin, or declarative fields.
    
-   Maintain **cross-platform** compatibility (Linux, macOS, Windows, UNIX).
    
-   Ensure strong **security, validation, and sanitization** guarantees.
    
-   Offer both **human-friendly** and **machine-readable** outputs.
    
-   Require **no external dependencies** beyond the Python standard library.
    

* * *

## Architecture Overview

`readwebform` consists of three major components:

1.  **CLI and Input Handling**
    
    -   Parses arguments.
        
    -   Loads HTML form content from one of several input modes.
        
    -   Validates and prepares HTML before serving.
        
2.  **Ephemeral Web Server**
    
    -   Binds to a local address (default `127.0.0.1`) and free port.
        
    -   Serves the form and processes POST submissions.
        
    -   Handles upload limits, validation, and feedback pages.
        
    -   Includes a per-session CSRF token for local hardening.
        
3.  **Result Output and Termination**
    
    -   Converts submission data into JSON or sanitized environment variables.
        
    -   Leaves uploaded files accessible in a temporary directory.
        
    -   **Terminates immediately** once a valid form submission is received and processed successfully.
        
        -   The HTTP server shuts down gracefully and releases the port.
            
        -   Invalid POSTs (e.g. CSRF mismatch or file too large) do not cause termination.
            
        -   Any browser reloads after completion will fail.
            

* * *

## Command-Line Interface

### Basic Invocation


```
readwebform [options]
```

### Key Options

| Option | Description |
| --- | --- |
| `--html "<string>"` | Inline HTML fragment or document containing one `<form>` element. (Highest precedence) |
| `--htmlfile <path>` | Path to an HTML file containing a form. |
| `--field <spec>` | Declaratively define form fields; may be specified multiple times. |
| *(default)* | Read HTML from stdin until EOF. |
| `--title <string>` | Page title shown above the form. |
| `--text <string>` | Instructional text shown above the form. |
| `--host <ip>` | Host/IP to bind to (default: `127.0.0.1`). |
| `--port <int>` | TCP port (optional; defaults to dynamic free port). |
| `--max-file-size <limit>` | Maximum individual upload size (e.g., `5M`, `200K`). |
| `--max-total-size <limit>` | Maximum total upload size (e.g., `20M`, `1G`). |
| `--timeout <seconds>` | Max time to wait for submission (default: `300`). |
| \`--reset-timeout-on-error \[true | false\]\` |
| `--json` | Output result as JSON (default output format). |
| `--envfile <path>` | Write sanitized `export` statements to file. |
| `--print-env` | Print sanitized environment variable exports to stdout. |
| `--launch-browser [path]` | Launch a web browser to open the form. If no path is provided, uses the system default browser. |
| `--no-submit-button` | Disable automatic submit button in declarative mode. |

* * *

## Input Modes and Precedence

| Priority | Mode | Description |
| --- | --- | --- |
| 1️⃣ | `--html` | Inline HTML document or fragment. |
| 2️⃣ | `--htmlfile` | Read form from file. |
| 3️⃣ | `--field` | Generate form dynamically. |
| 4️⃣ | stdin | Read HTML from standard input until EOF. |

### Mutual Exclusivity

-   `--html` and `--htmlfile` cannot be combined.
    
-   Only one source of form HTML is allowed per invocation.
    
-   Validation failure (no or multiple `<form>` elements) causes exit code `2`.
    

* * *

## Declarative Form Mode

Each field is specified as:


```
--field name:type[:label][:options]
```

### Example


```
--field name:text:Full+Name:required,placeholder=Your+name
--field age:number:Age:min=0,max=120
--field comments:textarea:Comments:rows=4
--field file:file:Upload:accept=.pdf,.docx
```

### Supported Field Types

`text`, `email`, `password`, `number`, `date`, `url`, `textarea`, `select`, `checkbox`, `file`

### Common Options

`required`, `placeholder`, `min`, `max`, `step`, `rows`, `cols`, `accept`, `multiple`, `options=a|b|c`

-   The form’s `enctype` is automatically set to `multipart/form-data` if any file fields are defined.
    
-   A submit button is **automatically added** unless `--no-submit-button` is provided.
    

* * *

## Upload Handling

-   Uploaded files are stored in a temporary directory (e.g. `/tmp/readwebform_XXXX`).
    
-   The directory name includes the random endpoint for traceability.
    
-   Files are **not automatically deleted** to allow downstream access.
    
-   File metadata (name and full path) is included in JSON output.
    

### Upload Limits

| Option | Purpose |
| --- | --- |
| `--max-file-size` | Limits the size of individual files. |
| `--max-total-size` | Limits total upload size of the entire request. |

-   Human-friendly units supported: `K`, `M`, `G`.
    
-   Pre-parse `Content-Length` check for total limit.
    
-   Per-file check after parsing each upload.
    

If exceeded:

-   HTTP 413 “Payload Too Large” response is sent.
    
-   User sees a clear error message in the browser.
    
-   The server logs the violation to stderr.
    
-   Exit code `6` is returned.
    

* * *

## Timeout Behavior

-   `--timeout` sets the maximum time (in seconds) that `readwebform` waits for a **valid submission**.
    
-   If expired before a successful submission, the program:
    
    -   Prints an error to stderr.
        
    -   Exits with code `5`.
        

### Reset Policy

Controlled by `--reset-timeout-on-error` (default `true`):

-   When enabled, any *recoverable* user interaction (e.g., validation error, too-large file) resets the timeout countdown.
    
-   When disabled, the timer runs continuously regardless of user activity.
    

* * *

## Browser Interaction

-   On launch, `readwebform` **prints the access URL** to stderr:
    
    
```json
    Open this URL in your browser:
      http://127.0.0.1:49162/readform_7a1f3c
```
    
-   By default, it does **not** open a browser automatically.
    
-   If `--launch-browser` is provided:
    
    -   With no argument → opens using the **system default browser**.
        
    -   With a path → executes that program to open the URL.
        
-   If the browser fails to launch, a warning is printed to stderr and the program continues normally.
    
-   Once a valid form is submitted:
    
    -   Displays a confirmation page:
        
        > “✅ Form submitted successfully. You may now close this window.”
        
    -   The server **terminates immediately**.
        

* * *

## Output Formats

### Default: JSON

The JSON output always includes all four keys for consistent parsing:

```json
{
  "success": true,
  "fields": {
    "name": "Alice",
    "email": "alice@example.com"
  },
  "files": {
    "resume": {
      "filename": "cv.pdf",
      "path": "/tmp/readwebform_b73j3d/cv.pdf"
    }
  },
  "error": null
}
```

On timeout or error:
```json
{
  "success": false,
  "fields": {},
  "files": {},
  "error": "timeout"
}
```

### Optional: Environment Variables

When `--envfile` or `--print-env` is used:


```bash
export WEBFORM_NAME='Alice'
export WEBFORM_EMAIL='alice@example.com'
```

### Sanitization Rules

-   All values are shell-escaped using `shlex.quote()`.
    
-   Newlines are converted to literal `\n`.
    
-   Control characters, backticks, and `$` are stripped or escaped.
    
-   Variable names must match `[A-Za-z_][A-Za-z0-9_]*`.
    
-   Warning shown if invalid names are skipped.
    
-   Explicit disclaimer:
    
    > “Environment files are for trusted local use only.  
    > Do not source envfiles generated from untrusted form data.”
    

* * *

## Validation Rules

-   Input must contain exactly **one `<form>`**.
    
-   External form `action` URLs are rejected.
    
-   Relative `action` attributes are patched to match the assigned endpoint.
    
-   Missing submit buttons produce a warning in manual HTML modes.
    
-   Validation failure exits with code `2`.
    

* * *

## Exit Codes

| Code | Meaning |
| --- | --- |
| 0 | Successful submission |
| 1 | Internal error |
| 2 | Invalid HTML or missing form |
| 3 | Could not read input file |
| 4 | Browser launch failure |
| 5 | Timeout waiting for submission |
| 6 | Upload size exceeded |

* * *

## Security Model

-   Defaults to binding on `127.0.0.1` (localhost only).
    
-   Rejects external requests unless `--host` explicitly allows them.
    
-   No remote fetches or includes.
    
-   No dynamic code execution or template evaluation.
    
-   All outputs sanitized and safely encoded.
    
-   Stateless and ephemeral by design.
    
-   Each session generates a **one-time-use CSRF token** using `secrets.token_hex(16)`, which:
    
    -   Is injected as a hidden `<input>` field in the served form.
        
    -   Must match on POST or submission is rejected with HTTP 403.
        
    -   Ensures only the fetched form can be validly submitted.
        

* * *

## Cross-Platform Behavior

| OS | Implementation Notes |
| --- | --- |
| Linux / macOS | Uses `webbrowser.open()` or subprocess for custom paths. |
| Windows | Uses `os.startfile()` or subprocess for provided executable. |
| Other UNIX | Same logic; pure standard library. |

* * *

## Example Workflows

### Inline HTML


```bash
readwebform --html '<form><input name="name" required><input type="submit"></form>'
```

### HTML File


```bash
readwebform --htmlfile ./templates/contact.html
```

### Declarative Mode


```bash
readwebform \
  --title "User Feedback" \
  --text "Please provide your name and upload your file." \
  --field name:text:Full+Name:required \
  --field upload:file:Attachment:accept=.pdf,.docx
```

### Launch in Default Browser


```bash
readwebform --htmlfile ./survey.html --launch-browser
```

### Launch in Firefox


```bash
readwebform --htmlfile ./survey.html --launch-browser /usr/bin/firefox
```

### JSON Output


```bash
data=$(readwebform --json)
echo "$data" | jq .
```

### Envfile Output


```
readwebform --envfile vars.env
source vars.env
```

* * *

## Future Enhancements (Post-MVP)

-   Markdown rendering in `--text`.
    
-   Redirect URLs after submission.
    
-   Persistent session reuse (multi-step forms).
    
-   Custom CSS and theme control.
    
-   Client-side form validation preview.
    
-   Optional authentication for remote use.
    

* * *

## Implementation Suggestions

-   **Language:** Python 3 (standard library only)
    
-   **Web Serving:** `http.server` + `socketserver.TCPServer`
    
-   **Form Parsing:** Implement minimal parsers for:
    
    -   `application/x-www-form-urlencoded` using `urllib.parse.parse_qs`
        
    -   `multipart/form-data` using manual boundary parsing or `email.parser`
        
    -   Avoid `cgi.FieldStorage` (deprecated in Python 3.13)
        
-   **CSRF Token:** Generate via `secrets.token_hex(16)`; inject in served form and validate on submission.
    
-   **Temporary Files:** `tempfile.mkdtemp()` for per-session directories.
    
-   **Argument Parsing:** `argparse`
    
-   **Output Serialization:** `json`, `shlex.quote()`
    
-   **HTML Validation:** `html.parser` or regex-based single-form check.
    
-   **Browser Launch:**
    
    -   If `--launch-browser` has no argument → `webbrowser.open(url)`
        
    -   If path provided → `subprocess.Popen([path, url])`
        
    -   Catch and log any errors without aborting.
        
-   **Timeout Management:**
    
    -   Track `server.last_activity` timestamp.
        
    -   Reset when `--reset-timeout-on-error` is true and user gets feedback.
        
-   **Port Allocation:** Bind to `('', 0)` to auto-select a free port.
    
-   **Graceful Shutdown:**
    
    -   On successful submission → call `server.shutdown()` and exit 0.
        
    -   On timeout → exit 5 after printing to stderr.
        
-   **Testing:**
    
    -   Validate multipart limits and boundary parsing.
        
    -   Verify behavior on Windows, macOS, and Linux.
        

* * *

## Summary

`readwebform` is a secure, ephemeral, and script-friendly way to prompt users via the web for structured input.  
It terminates automatically upon successful submission, producing JSON or environment-variable output suitable for shell pipelines and automation.

It embodies these principles:

-   **Explicit over implicit.**
    
-   **Stateless and ephemeral.**
    
-   **Safe by default.**
    
-   **Cross-platform simplicity.**
    

* * *

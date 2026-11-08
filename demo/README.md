# Regenerating demo.gif

`record_demo.py` records the README demo without any manual screen capture.
It runs a real `readwebform` process, drives a real Chromium through the
form with Playwright, and records a single page that shows a terminal pane
and a browser pane side by side. ffmpeg turns the recording into a GIF.

```bash
# once: fetch Chromium for Playwright (cached under your home directory)
uv run --with playwright playwright install chromium

# each release: from the repository root
uv run --with playwright python demo/record_demo.py
```

Options: `--out <path>` (default `demo.gif`), `--keep-video` to keep the
`.webm`, `--headed` to watch the recording for debugging.

To change what the demo shows, edit `DEMO_ARGS` (the command that is run),
`COMMAND_LINES` (how it is displayed) and the fill-in steps in `main()`.
Playwright is only a dependency of this script, not of readwebform.

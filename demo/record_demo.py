"""
Regenerate demo.gif.

One headless Chromium page is recorded. It contains two panes:

* a fake terminal (left) that "types" the readwebform command, shows the
  URL the real process prints, and finally shows the real JSON it outputs;
* a browser frame (right) whose <iframe> loads the real form served by a
  real `readwebform` subprocess, filled in by Playwright.

The recording is converted to a GIF with ffmpeg.

Usage (from the repository root):

    uv run --with playwright playwright install chromium   # once
    uv run --with playwright python demo/record_demo.py [--out demo.gif] [--keep-video]

Requires ffmpeg on PATH for the GIF step.
"""

import argparse
import html
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / 'src'

WIDTH, HEIGHT = 1200, 620
GIF_WIDTH = 960
GIF_FPS = 10

# The command we demonstrate (shown verbatim in the terminal pane and
# actually run, minus the python -m prefix).
DEMO_ARGS = [
    '--title', 'Release checklist',
    '--submit-label', 'Ship it',
    '--field', 'version:text:Version:required,placeholder=e.g. 2.1.0',
    '--field', 'env:select:Environment:options=staging|production',
    '--field', 'notify:checkbox:Notify the team:checked',
    '--field', 'mode:radio:Rollout:options=canary|full,value=canary',
]

# Terminal typing: how the command is rendered (line breaks for readability)
COMMAND_LINES = [
    'readwebform --title "Release checklist" --submit-label "Ship it" \\',
    '  --field "version:text:Version:required,placeholder=e.g. 2.1.0" \\',
    '  --field "env:select:Environment:options=staging|production" \\',
    '  --field "notify:checkbox:Notify the team:checked" \\',
    '  --field "mode:radio:Rollout:options=canary|full,value=canary"',
]

PAGE_HTML = r"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  html, body { margin: 0; height: 100%; background: #1e1f22; font-family: system-ui, -apple-system, sans-serif; }
  #wrap { display: flex; height: 100%; gap: 14px; padding: 14px; box-sizing: border-box; }
  .pane { border-radius: 10px; overflow: hidden; box-shadow: 0 6px 24px rgba(0,0,0,.45); display: flex; flex-direction: column; }
  #term { flex: 0 0 610px; background: #0f1115; color: #d6d8de; }
  #browser { flex: 1; background: #fff; opacity: 0; transition: opacity .4s; }
  #browser.on { opacity: 1; }
  .bar { height: 38px; display: flex; align-items: center; padding: 0 12px; gap: 8px; }
  #term .bar { background: #23252b; }
  #browser .bar { background: #e9e9ec; }
  .dot { width: 12px; height: 12px; border-radius: 50%; }
  .r { background: #ff5f57; } .y { background: #febc2e; } .g { background: #28c840; }
  .title { flex: 1; text-align: center; font-size: 13px; color: #9a9ca3; }
  #url { flex: 1; background: #fff; border-radius: 6px; height: 24px; line-height: 24px; font-size: 13px; color: #333; padding: 0 10px; margin-left: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  pre { margin: 0; padding: 14px 16px; font: 13.5px/1.45 "SF Mono", Menlo, Consolas, monospace; white-space: pre-wrap; word-break: break-all; flex: 1; overflow: hidden; }
  .prompt { color: #7ee787; } .cmd { color: #e6edf3; } .dim { color: #8b949e; } .url { color: #79c0ff; }
  .k { color: #ff7b72; } .s { color: #a5d6ff; } .n { color: #d2a8ff; }
  .cursor { display: inline-block; width: 8px; height: 15px; background: #d6d8de; vertical-align: -2px; animation: blink 1s steps(1) infinite; }
  @keyframes blink { 50% { opacity: 0; } }
  iframe { border: 0; flex: 1; width: 100%; }
</style></head><body>
<div id="wrap">
  <div id="term" class="pane">
    <div class="bar"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span><span class="title">zsh</span></div>
    <pre id="out"><span class="prompt">$ </span><span id="typed" class="cmd"></span><span class="cursor"></span></pre>
  </div>
  <div id="browser" class="pane">
    <div class="bar"><span class="dot r"></span><span class="dot y"></span><span class="dot g"></span><span id="url"></span></div>
    <iframe id="frame"></iframe>
  </div>
</div>
<script>
  const out = document.getElementById('out');
  // Type the command inside the page so the animation's duration does not
  // depend on round trips from the recorder; resolves when finished.
  window.typeLines = (lines, charMs, lineMs) => new Promise((resolve) => {
    const typed = document.getElementById('typed');
    const text = lines.join('\n');
    let i = 0;
    const step = () => {
      if (i >= text.length) { resolve(); return; }
      const c = text[i++];
      typed.textContent += c;
      setTimeout(step, c === '\n' ? lineMs : charMs);
    };
    step();
  });
  window.append = (htmlText) => {
    const cur = out.querySelector('.cursor'); if (cur) cur.remove();
    out.insertAdjacentHTML('beforeend', htmlText);
    out.insertAdjacentHTML('beforeend', '<span class="cursor"></span>');
  };
  window.openBrowser = (url) => {
    document.getElementById('url').textContent = url;
    document.getElementById('frame').src = url;
    document.getElementById('browser').classList.add('on');
  };
</script>
</body></html>"""


def colour_json(text: str) -> str:
    """Very small JSON syntax highlighter for the terminal pane."""
    import re
    esc = html.escape(text)
    esc = re.sub(r'(&quot;[^&]*?&quot;)(\s*:)', r'<span class="k">\1</span>\2', esc)
    esc = re.sub(r'(:\s*)(&quot;.*?&quot;)', r'\1<span class="s">\2</span>', esc)
    esc = re.sub(r'(:\s*)(true|false|null|\d+)', r'\1<span class="n">\2</span>', esc)
    return esc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', default=str(ROOT / 'demo.gif'), help='GIF path (default: demo.gif in repo root)')
    parser.add_argument('--keep-video', action='store_true', help='Keep the intermediate .webm next to the GIF')
    parser.add_argument('--headed', action='store_true', help='Show the browser while recording (debugging)')
    args = parser.parse_args()

    if shutil.which('ffmpeg') is None:
        print('ffmpeg not found on PATH; it is needed to produce the GIF', file=sys.stderr)
        return 2

    workdir = Path(tempfile.mkdtemp(prefix='readwebform_demo_'))
    url_file = workdir / 'url.txt'

    # CI=1 stops the real process from opening a real browser; the browser
    # pane in the recording plays that role.
    env = dict(os.environ, PYTHONPATH=str(SRC), PYTHONUNBUFFERED='1', CI='1')
    proc = subprocess.Popen(
        [sys.executable, '-m', 'readwebform', *DEMO_ARGS, '--timeout', '120',
         '--url-file', str(url_file), '--upload-dir', str(workdir / 'uploads')],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env,
    )
    video_path = None
    try:
        deadline = time.time() + 10
        url = ''
        while not url and time.time() < deadline:
            if proc.poll() is not None:
                print('readwebform exited early:', proc.stderr.read(), file=sys.stderr)
                return 1
            if url_file.exists():
                url = url_file.read_text().strip()
            if not url:
                time.sleep(0.05)
        if not url:
            proc.kill()
            _, err = proc.communicate(timeout=5)
            print('readwebform did not report a URL within 10 s:', err, file=sys.stderr)
            return 1

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=not args.headed)
            context = browser.new_context(
                viewport={'width': WIDTH, 'height': HEIGHT},
                device_scale_factor=1,
                record_video_dir=str(workdir),
                record_video_size={'width': WIDTH, 'height': HEIGHT},
            )
            page = context.new_page()
            page.set_content(PAGE_HTML)
            page.wait_for_timeout(700)

            # 1. Type the command (awaits the in-page animation)
            page.evaluate('lines => typeLines(lines, 18, 120)', COMMAND_LINES)
            page.wait_for_timeout(500)

            # 2. The process prints the URL (this is what it really printed)
            page.evaluate('t => append(t)',
                          '\n\n<span class="dim">Open this URL in your browser:</span>\n'
                          f'  <span class="url">{html.escape(url)}</span>\n\n')
            page.wait_for_timeout(900)

            # 3. The browser opens the form
            page.evaluate('u => openBrowser(u)', url)
            frame = page.frame_locator('#frame')
            frame.locator('input[name=version]').wait_for()
            page.wait_for_timeout(900)

            # 4. Fill it in like a person would
            frame.locator('input[name=version]').click()
            frame.locator('input[name=version]').press_sequentially('2.1.0', delay=110)
            page.wait_for_timeout(400)
            frame.locator('select[name=env]').select_option('production')
            page.wait_for_timeout(500)
            frame.locator('input[name=mode][value=full]').check()
            page.wait_for_timeout(700)
            frame.locator('button[type=submit]:not(.cancel)').hover()
            page.wait_for_timeout(300)
            frame.locator('button[type=submit]:not(.cancel)').click()

            # 5. Process exits with the JSON
            stdout, _ = proc.communicate(timeout=10)
            frame.locator('text=Form submitted successfully').wait_for()
            page.wait_for_timeout(500)
            result = json.loads(stdout)
            pretty = json.dumps(result, indent=2)
            page.evaluate('t => append(t)', colour_json(pretty) + '\n<span class="prompt">$ </span>')
            page.wait_for_timeout(3200)

            context.close()  # flushes the video
            video_path = Path(page.video.path())
            browser.close()
        out = Path(args.out)
        cmd = [
            'ffmpeg', '-loglevel', 'error', '-y', '-ss', '0.4', '-i', str(video_path),
            '-vf', f'fps={GIF_FPS},scale={GIF_WIDTH}:-1:flags=lanczos,'
                   'split[a][b];[a]palettegen=max_colors=160:stats_mode=diff[p];'
                   '[b][p]paletteuse=dither=bayer:bayer_scale=5:diff_mode=rectangle',
            '-loop', '0', str(out),
        ]
        subprocess.run(cmd, check=True)
        if args.keep_video:
            shutil.copy(video_path, out.with_suffix('.webm'))
        print(f'wrote {out} ({out.stat().st_size // 1024} KB)')
        return 0
    finally:
        if proc.poll() is None:
            proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == '__main__':
    sys.exit(main())

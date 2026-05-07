"""Black-box integration test for the MRU Tab Switcher in Safari.

Drives Safari through AppleScript (osascript): opens tabs at a local
http server, activates them in a known order, then simulates Ctrl+1
(toggle) and Ctrl+Q (picker) keystrokes via System Events. Verifies
the visible state afterward.

Why AppleScript and not Playwright/safaridriver? Playwright's WebKit
target is not Safari proper and won't load Safari Web Extensions.
safaridriver works but requires Selenium and "Allow Remote Automation".
AppleScript is built into macOS, callable from `osascript` (so the
agent or any shell can invoke it), and reads/drives the real Safari
where the extension is installed. Trade-off: like safaridriver, it's
black-box — no service-worker introspection, no `chrome.storage`
peek. For this extension that's enough; the assertions are about
visible behavior (which tab activates).

Prerequisites:
  - Safari has MRU Tab Switcher installed AND enabled.
  - Shortcuts bound in Safari -> Settings -> Extensions:
      * Switch to most recently used tab (instant toggle): Control+1
      * Open recent-tabs picker:                            Control+Q
  - "Allow Unsigned Extensions" on (Develop -> Developer Settings)
    if the extension isn't signed with a paid Developer ID.
  - macOS Accessibility / Automation permission for the process
    invoking osascript (first run will prompt).

Run with: python scripts/test_mru_safari.py
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


# ---------- localhost http server (real-origin URLs, like test_mru.py) ----------

class _TabHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        try:
            n = int(self.path.rsplit("/", 1)[-1])
        except ValueError:
            n = 0
        hue = (n * 67) % 360
        body = (
            f"<!doctype html><html><head><meta charset=utf-8>"
            f"<title>Tab {n}</title>"
            f"<style>body{{margin:0;height:100vh;display:flex;align-items:center;"
            f"justify-content:center;font:48px/1 -apple-system,sans-serif;"
            f"color:#fff;background:hsl({hue},70%,40%)}}</style></head>"
            f"<body>Tab {n}</body></html>"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())

    def log_message(self, *a, **k):
        pass


def _start_server() -> int:
    s = HTTPServer(("127.0.0.1", 0), _TabHandler)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    return s.server_address[1]


PORT = _start_server()
URLS = [f"http://127.0.0.1:{PORT}/tab/{i + 1}" for i in range(4)]


# ---------- AppleScript helpers ----------

def osa(script: str) -> str:
    """Run an AppleScript and return its stdout (stripped). Raises on error."""
    p = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if p.returncode != 0:
        raise RuntimeError(f"osascript failed:\n  script: {script}\n  stderr: {p.stderr}")
    return p.stdout.strip()


def safari_open_tabs(urls: list[str]) -> None:
    """Open a fresh Safari window at urls[0], then add the rest as tabs."""
    osa('tell application "Safari" to activate')
    time.sleep(0.4)
    osa(f'tell application "Safari" to make new document with properties {{URL:"{urls[0]}"}}')
    time.sleep(0.6)
    for u in urls[1:]:
        osa(
            f'tell application "Safari" to tell front window '
            f'to make new tab with properties {{URL:"{u}"}}'
        )
        time.sleep(0.45)


def safari_activate_tab(idx: int) -> None:
    """Set the active tab in the front window to tab `idx` (1-based)."""
    osa(f'tell application "Safari" to set current tab of front window to tab {idx} of front window')


def safari_active_url() -> str:
    return osa('tell application "Safari" to URL of current tab of front window')


def safari_close_test_window() -> None:
    osa('tell application "Safari" to close front window saving no')


def safari_picker_visible() -> bool:
    """Return True iff any Safari window is currently showing the picker page."""
    out = osa(
        'tell application "Safari"\n'
        '  set hasExt to false\n'
        '  repeat with w in windows\n'
        '    try\n'
        '      set u to URL of current tab of w\n'
        '      if u contains "picker.html" then set hasExt to true\n'
        '    end try\n'
        '  end repeat\n'
        '  return hasExt as text\n'
        'end tell'
    )
    return out == "true"


def send_keys(key_code: int, with_ctrl: bool = True) -> None:
    """Send a single keystroke through System Events."""
    mods = "control down" if with_ctrl else ""
    if mods:
        osa(f'tell application "System Events" to key code {key_code} using {{{mods}}}')
    else:
        osa(f'tell application "System Events" to key code {key_code}')


# ---------- the test ----------

def main() -> int:
    results: list[tuple[str, bool]] = []
    print(f"[setup] urls: {URLS}")

    safari_open_tabs(URLS)
    time.sleep(0.5)

    # Activate tabs in order 1 -> 2 -> 3 -> 4. Active is now tab 4.
    for idx in (1, 2, 3, 4):
        safari_activate_tab(idx)
        time.sleep(0.45)

    cur = safari_active_url()
    print(f"[seed] active after 1..4 sequence: {cur}")
    results.append(("starts on tab 4", cur.rstrip("/").endswith("/tab/4")))

    # Ctrl+1 -> toggle to previous tab (tab 3).
    send_keys(18, with_ctrl=True)  # key code 18 = '1'
    time.sleep(0.6)
    after1 = safari_active_url()
    print(f"[Ctrl+1 #1] active: {after1}")
    results.append(("Ctrl+1 toggles to tab 3", after1.rstrip("/").endswith("/tab/3")))

    # Ctrl+1 again -> toggle back to tab 4.
    send_keys(18, with_ctrl=True)
    time.sleep(0.6)
    after2 = safari_active_url()
    print(f"[Ctrl+1 #2] active: {after2}")
    results.append(("Ctrl+1 toggles back to tab 4", after2.rstrip("/").endswith("/tab/4")))

    # Ctrl+Q -> picker should open. We can't introspect picker state from
    # outside Safari, but we can check whether *some* window is showing
    # picker.html.
    send_keys(12, with_ctrl=True)  # key code 12 = 'q'
    time.sleep(0.9)
    visible = safari_picker_visible()
    print(f"[Ctrl+Q] picker.html visible: {visible}")
    results.append(("Ctrl+Q opens the picker", visible))

    # Dismiss the picker with Esc (key code 53), no modifier.
    send_keys(53, with_ctrl=False)
    time.sleep(0.4)

    # Tear down our test window so we don't leave 4 tabs lying around.
    try:
        safari_close_test_window()
    except Exception:
        pass

    print()
    all_pass = True
    for name, ok in results:
        marker = "PASS" if ok else "FAIL"
        if not ok:
            all_pass = False
        print(f"  [{marker}] {name}")
    print()
    print("OVERALL:", "PASS" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as e:
        print(f"\nFAILED: {e}", file=sys.stderr)
        print(
            "\nIf the error mentions 'not allowed' or '-1743', grant the "
            "calling process (Terminal/iTerm/etc.) Accessibility & "
            "Automation permission in System Settings -> Privacy & "
            "Security, then re-run.",
            file=sys.stderr,
        )
        sys.exit(2)

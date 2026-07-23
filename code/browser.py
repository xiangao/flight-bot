"""Headful Chrome launcher for flight-bot.

Provides one public helper:

    launch_browser() -> (playwright, browser, page)
        Starts Xvfb (if needed), launches google-chrome with --use-angle=vulkan
        for real-GPU WebGL on a virtual display, connects Playwright over CDP,
        and returns a ready Page.

Vendored from jal-bot's code/browser.py (2026-07-23), itself adapted from
rental-car-bot/code/costco_searcher.py — see
docs/superpowers/specs/2026-07-23-scraping-rewrite-design.md. CDP_PORT,
CHROME_PROFILE, and _XVFB_DISPLAY are changed from jal-bot's values (9223,
chrome-jalbot, :50 — already shared with jal-shanghai-bot) to avoid any
collision if flight-bot's and jal-bot's independent daily timers ever overlap
in wall-clock time.
"""

import os
import shutil
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Tuple

from playwright.sync_api import Page, sync_playwright

# ── CDP / profile config ──────────────────────────────────────────────────────

CDP_PORT = 9224                                         # distinct from jal-bot/jal-shanghai-bot's 9223
CDP_URL  = f"http://localhost:{CDP_PORT}"
CHROME_PROFILE = Path.home() / ".cache" / "chrome-flightbot"

# Virtual display geometry; Xvfb is allocated lazily (once per process).
_XVFB_DISPLAY = ":51"                                   # distinct from jal-bot's :50
_XVFB_PROC: subprocess.Popen | None = None


# ── Xvfb ─────────────────────────────────────────────────────────────────────

def _ensure_xvfb() -> str:
    """Start our own Xvfb virtual display if it isn't already running.

    Returns the DISPLAY string (e.g. ":51") to pass to Chrome.

    Deliberately ignores any inherited `DISPLAY` from the environment. Under
    the systemd --user timer, `DISPLAY=:1` (the real logged-in desktop) is
    imported into the user session's environment, so reusing it pops a
    visible Chrome window on the user's screen on every scheduled run.
    `--use-angle=vulkan` gives real-GPU WebGL on a virtual display regardless,
    so there's never a reason to touch the real one.
    """
    global _XVFB_PROC

    if _XVFB_PROC is not None and _XVFB_PROC.poll() is None:
        return _XVFB_DISPLAY

    xvfb = shutil.which("Xvfb")
    if xvfb is None:
        raise RuntimeError(
            "Xvfb not found.  Install xvfb (apt install xvfb) or set DISPLAY "
            "to an active X display before calling launch_browser()."
        )

    _XVFB_PROC = subprocess.Popen(
        [xvfb, _XVFB_DISPLAY, "-screen", "0", "1920x1080x24"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(1.0)
    return _XVFB_DISPLAY


# ── Chrome CDP management ─────────────────────────────────────────────────────

def _chrome_up() -> bool:
    """True if a Chrome DevTools endpoint is already listening on the CDP port."""
    try:
        urllib.request.urlopen(f"{CDP_URL}/json/version", timeout=2)
        return True
    except Exception:
        return False


def _ensure_chrome() -> None:
    """Make sure a real, headful google-chrome is listening on the CDP port.

    Reuses an already-running instance so cookies warm across runs.
    Otherwise launches one against CHROME_PROFILE with --use-angle=vulkan,
    which routes WebGL through the GPU via Vulkan directly from /dev/dri —
    bypassing the X server — so WebGL is real even on an Xvfb virtual display.
    """
    if _chrome_up():
        return

    display = _ensure_xvfb()

    chrome = shutil.which("google-chrome") or "/usr/bin/google-chrome"
    CHROME_PROFILE.mkdir(parents=True, exist_ok=True)

    # A stale singleton lock makes a second launch silently forward to the old
    # instance and DROP --remote-debugging-port, so clear it first.
    for lock in CHROME_PROFILE.glob("Singleton*"):
        try:
            lock.unlink()
        except OSError:
            pass

    env = dict(os.environ)
    env["DISPLAY"] = display

    subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={CHROME_PROFILE}",
            "--use-angle=vulkan",          # real-GPU WebGL on a virtual display
            "--no-first-run",
            "--no-default-browser-check",
            "about:blank",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,            # outlive this process for cookie reuse
        env=env,
    )

    for _ in range(40):
        if _chrome_up():
            return
        time.sleep(0.5)

    raise RuntimeError(
        f"Chrome did not come up on {CDP_URL}.  "
        f"Headful Chrome needs an X display (DISPLAY={display}); "
        f"check that Xvfb started and that google-chrome is installed."
    )


# ── Public API ────────────────────────────────────────────────────────────────

def launch_browser() -> Tuple[object, object, Page]:
    """Launch a headful Chrome on a virtual Xvfb display and return a Playwright Page.

    Returns:
        (playwright, browser, page) — a 3-tuple where *page* is ready for
        navigation. The caller is responsible for closing *browser* (and
        optionally stopping Playwright) when done.

    Chrome is connected over CDP and reused across calls within the same
    process (and across daily runs if chrome-flightbot is already running).
    """
    _ensure_chrome()
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(CDP_URL)
    context = browser.contexts[0] if browser.contexts else browser.new_context()
    page = context.new_page()
    return pw, browser, page

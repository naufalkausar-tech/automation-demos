"""Headless Chrome as the HTML -> PDF/PNG engine: the same Blink engine that shapes Arabic in the browser.

Each render gets a throwaway profile dir so it never touches (or waits for) the user's own Chrome session.
The PNG is taken at A4 width and as many A4 heights as the PDF has pages, so it shows the whole report.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CANDIDATES = (
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
)
PAGE_WIDTH_PX, PAGE_HEIGHT_PX = 794, 1123  # A4 at 96 CSS px per inch
SCALE = 2  # PNG pixel density
MAX_PNG_PAGES = 5
TIMEOUT = 60


class ChromeNotFound(RuntimeError):
    pass


class RenderError(RuntimeError):
    pass


def find_chrome(env: dict[str, str] | None = None) -> str:
    env = os.environ if env is None else env
    configured = env.get("CHROME_PATH")
    if configured:
        if Path(configured).is_file():
            return configured
        raise ChromeNotFound(f"CHROME_PATH points to {configured!r}, which does not exist")
    for path in CANDIDATES:
        if Path(path).is_file():
            return path
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if found := shutil.which(name):
            return found
    raise ChromeNotFound("Google Chrome / Chromium not found: install it or set CHROME_PATH to the executable")


def base_command(chrome: str, profile_dir: str | Path) -> list[str]:
    return [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            "--disable-extensions", f"--user-data-dir={profile_dir}"]


def pdf_command(chrome: str, html_path: Path, pdf_path: Path, profile_dir: str | Path) -> list[str]:
    return [*base_command(chrome, profile_dir), "--no-pdf-header-footer", f"--print-to-pdf={pdf_path}",
            Path(html_path).resolve().as_uri()]


def png_command(chrome: str, html_path: Path, png_path: Path, profile_dir: str | Path, pages: int = 1) -> list[str]:
    return [*base_command(chrome, profile_dir), "--hide-scrollbars", f"--force-device-scale-factor={SCALE}",
            f"--window-size={PAGE_WIDTH_PX},{PAGE_HEIGHT_PX * pages}", f"--screenshot={png_path}",
            Path(html_path).resolve().as_uri()]


def pdf_page_count(pdf_path: Path) -> int:
    # Chrome (Skia) writes page objects uncompressed; "/Type /Pages" is the page tree, not a page
    return max(1, len(re.findall(rb"/Type\s*/Page[^s]", Path(pdf_path).read_bytes())))


def _run(command: list[str], output: Path, timeout: float, run) -> None:
    try:
        result = run(command, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RenderError(f"Chrome did not finish within {timeout:g}s") from exc
    if result.returncode != 0 or not output.is_file() or output.stat().st_size == 0:
        detail = (result.stderr or b"").decode("utf-8", "replace").strip()[-500:]
        raise RenderError(f"Chrome could not write {output.name} (exit code {result.returncode}): {detail}")


def render_files(html_path: str | Path, pdf_path: str | Path, png_path: str | Path, chrome: str | None = None,
                 timeout: float = TIMEOUT, run=subprocess.run) -> None:
    """Write the PDF and the PNG for one HTML file. Raises ChromeNotFound or RenderError."""
    chrome = chrome or find_chrome()
    pdf_path, png_path = Path(pdf_path).resolve(), Path(png_path).resolve()
    for stale in (pdf_path, png_path):  # a leftover file must never pass as this run's output
        stale.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="arabic_report_chrome_", ignore_cleanup_errors=True) as profile:
        _run(pdf_command(chrome, html_path, pdf_path, profile), pdf_path, timeout, run)
        pages = min(pdf_page_count(pdf_path), MAX_PNG_PAGES)
        _run(png_command(chrome, html_path, png_path, profile, pages), png_path, timeout, run)

"""HTML card -> PNG still and frame-by-frame MP4 (spec §5: 1080×1350, 30 fps, H.264 yuv420p).

Frames come from the template's `setT(t)`; Chrome screenshots are piped straight into ffmpeg, so no frame touches disk.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

TEMPLATES = Path(__file__).parent / "templates"
WIDTH, HEIGHT, FPS = 1080, 1350, 30


def page_file(template: str, data: dict, out_dir: Path, name: str) -> Path:
    """The template with its data, written next to the bundled fonts' relative path."""
    html = (TEMPLATES / template).read_text(encoding="utf-8").replace("__DATA__", json.dumps(data, ensure_ascii=False), 1)
    path = out_dir / f"{name}.html"
    fonts = out_dir / "fonts"
    if not fonts.exists():
        fonts.mkdir(parents=True, exist_ok=True)
        for font in (TEMPLATES / "fonts").iterdir():
            (fonts / font.name).write_bytes(font.read_bytes())
    path.write_text(html, encoding="utf-8")
    return path


def _browser(playwright):
    # CONTENT_CHROME_CHANNEL=chrome uses an installed Chrome (local runs); the worker image ships Playwright's own shell.
    return playwright.chromium.launch(channel=os.environ.get("CONTENT_CHROME_CHANNEL") or None, args=["--disable-dev-shm-usage", "--no-sandbox", "--font-render-hinting=none"])


def _open(browser, path: Path):
    page = browser.new_page(viewport={"width": WIDTH, "height": HEIGHT})
    page.goto(path.as_uri())
    page.wait_for_load_state("networkidle", timeout=30000)
    page.evaluate("document.fonts.ready")
    return page


def _shot(page) -> bytes:
    """One frame; a screenshot that stalls once (seen on a busy machine) is taken again."""
    try:
        return page.screenshot(type="jpeg", quality=95, timeout=20000)
    except Exception:  # noqa: BLE001
        return page.screenshot(type="jpeg", quality=95, timeout=60000)


def still(template: str, data: dict, out_dir: Path, name: str) -> Path:
    from playwright.sync_api import sync_playwright

    path = page_file(template, data, out_dir, name)
    png = out_dir / f"{name}.png"
    with sync_playwright() as p:
        browser = _browser(p)
        try:
            page = _open(browser, path)
            page.screenshot(path=str(png))
        finally:
            browser.close()
    return png


def video(template: str, data: dict, out_dir: Path, name: str, seconds: float) -> tuple[Path, Path, Path, Path]:
    """Silent animation, then the final frame as PNG still, JPEG cover and a ≤320 px JPEG thumbnail."""
    from playwright.sync_api import sync_playwright

    path = page_file(template, data, out_dir, name)
    mp4, png = out_dir / f"{name}_silent.mp4", out_dir / f"{name}.png"
    encoder = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "20", "-preset", "medium", "-threads", "2", "-movflags", "+faststart", str(mp4)],
        stdin=subprocess.PIPE,
    )
    try:
        with sync_playwright() as p:
            browser = _browser(p)
            try:
                page = _open(browser, path)
                for i in range(int(FPS * seconds)):
                    page.evaluate(f"setT({i / FPS})")
                    encoder.stdin.write(_shot(page))
                page.evaluate("setT(99)")
                page.screenshot(path=str(png))
            finally:
                browser.close()
    finally:
        encoder.stdin.close()
        if encoder.wait(timeout=600) != 0:
            raise RuntimeError(f"ffmpeg failed on {name}")
    cover, thumb = out_dir / f"{name}_cover.jpg", out_dir / f"{name}_thumb.jpg"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(png), "-q:v", "3", str(cover)], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(png), "-vf", "scale=256:320", "-q:v", "5", str(thumb)], check=True)
    return mp4, png, cover, thumb


def duration(path: Path) -> float:
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)])
    return float(out.decode().strip())

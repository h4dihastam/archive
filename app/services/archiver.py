from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from app.config import settings
from app.models import ArchiveArtifact


def _safe_slug(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace(":", "_")
    path = parsed.path.strip("/").replace("/", "_") or "root"
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{host}_{path}_{ts}"


class Archiver:
    async def archive(self, url: str) -> ArchiveArtifact:
        slug = _safe_slug(url)
        folder = Path(settings.base_storage_dir) / slug
        folder.mkdir(parents=True, exist_ok=True)

        raw_html_path = folder / "raw.html"
        rendered_html_path = folder / "rendered.html"
        screenshot_path = folder / "screenshot.png"

        async with httpx.AsyncClient(timeout=settings.request_timeout, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            raw_html_path.write_text(response.text, encoding="utf-8")

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch()
                page = await browser.new_page(viewport={"width": 1440, "height": 2200})
                await page.goto(url, wait_until="networkidle", timeout=settings.playwright_timeout_ms)
                rendered = await page.content()
                rendered_html_path.write_text(rendered, encoding="utf-8")
                await page.screenshot(path=str(screenshot_path), full_page=True)
                await browser.close()
        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            rendered_html_path.write_text(
                f"<!-- Playwright render failed: {exc} -->\n" + raw_html_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            screenshot_path.write_bytes(b"")

        return ArchiveArtifact(
            url=url,
            created_at=datetime.now(UTC),
            folder=folder,
            raw_html_path=raw_html_path,
            rendered_html_path=rendered_html_path,
            screenshot_path=screenshot_path,
        )

"""
Archiver — فیکس کامل اسکرین‌شات سفید + آرشیو کامل صفحه
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

from app.config import settings
from app.models import ArchiveArtifact

logger = logging.getLogger(__name__)

def _safe_slug(url: str) -> str:
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.netloc.replace(":", "_").replace(".", "_")
    path = parsed.path.strip("/").replace("/", "_") or "page"
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{host}_{path}_{ts}"[:100]

class Archiver:
    async def archive(self, url: str) -> ArchiveArtifact:
        slug = _safe_slug(url)
        folder = Path(settings.base_storage_dir) / slug
        folder.mkdir(parents=True, exist_ok=True)

        raw_html_path = folder / "raw.html"
        rendered_html_path = folder / "archive.html"
        screenshot_path = folder / "screenshot.png"

        post_meta = {"title": ""}

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                    "--disable-web-security",
                    "--single-process",
                ]
            )

            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 1200},
                device_scale_factor=1,
            )

            page = await context.new_page()

            try:
                logger.info(f"→ آرشیو صفحه: {url}")

                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(6000)  # صبر برای JS و لود محتوا

                # اسکرول تدریجی برای لود تمام lazy content
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.4)")
                await page.wait_for_timeout(2000)
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.8)")
                await page.wait_for_timeout(2000)
                await page.evaluate("window.scrollTo(0, 0)")

                title = await page.title() or "Archived Page"
                post_meta["title"] = title

                # === فیکس اصلی: اسکرین‌شات کامل ===
                await page.screenshot(
                    path=str(screenshot_path),
                    full_page=True,
                    timeout=30000
                )
                logger.info(f"✅ اسکرین‌شات گرفته شد: {screenshot_path.stat().st_size / 1024:.1f} KB")

                html_content = await page.content()

            except PlaywrightTimeout:
                logger.warning("Timeout — محتوای موجود رو ذخیره می‌کنم")
                html_content = await page.content()
                await page.screenshot(path=str(screenshot_path), full_page=True)
            except Exception as e:
                logger.error(f"خطا در آرشیو {url}: {e}")
                html_content = f"<h1>خطا در آرشیو</h1><p>{e}</p>"
                screenshot_path.write_bytes(b"")
            finally:
                await browser.close()

        # بنر Archive Hub (مثل archive.is)
        banner = f'''
<div style="position:fixed;top:0;left:0;right:0;z-index:999999;background:#1e3a8a;color:white;padding:16px 24px;font-family:system-ui;box-shadow:0 4px 20px rgba(0,0,0,0.6);display:flex;align-items:center;justify-content:space-between;font-size:15px;">
  <div>📦 <strong>Archive Hub</strong> — آرشیو کامل صفحه</div>
  <div>{datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")}</div>
  <a href="{url}" target="_blank" style="color:#93c5fd;">🔗 لینک اصلی</a>
</div>
<div style="height:80px;"></div>
'''
        final_html = html_content.replace("</body>", banner + "</body>", 1) if "</body>" in html_content else banner + html_content

        raw_html_path.write_text(html_content, encoding="utf-8")
        rendered_html_path.write_text(final_html, encoding="utf-8")

        return ArchiveArtifact(
            url=url,
            created_at=datetime.now(UTC),
            folder=folder,
            raw_html_path=raw_html_path,
            rendered_html_path=rendered_html_path,
            screenshot_path=screenshot_path,
            post_meta=post_meta,
        )
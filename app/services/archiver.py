"""
Archiver — فیکس نهایی برای Render Free + X.com
Playwright فقط برای HTML کامل + thum.io برای اسکرین‌شات (بدون تایم‌اوت)
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
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

async def _get_screenshot(url: str) -> bytes:
    """thum.io — سریع و مطمئن روی Render Free"""
    encoded = quote(url, safe="")
    try:
        async with httpx.AsyncClient(timeout=25) as c:
            r = await c.get(f"https://image.thum.io/get/width/1280/crop/900/noanimate/allowJPG/{encoded}")
            if r.status_code == 200 and len(r.content) > 5000:
                logger.info(f"✅ اسکرین‌شات از thum.io: {len(r.content)/1024:.1f} KB")
                return r.content
    except Exception as e:
        logger.warning(f"thum.io failed: {e}")
    return b""

class Archiver:
    async def archive(self, url: str) -> ArchiveArtifact:
        slug = _safe_slug(url)
        folder = Path(settings.base_storage_dir) / slug
        folder.mkdir(parents=True, exist_ok=True)

        raw_html_path = folder / "raw.html"
        rendered_html_path = folder / "archive.html"
        screenshot_path = folder / "screenshot.png"

        post_meta = {"title": ""}

        # ── ۱. HTML کامل با Playwright (بدون اسکرین‌شات سنگین) ──
        html_content = ""
        title = "Archived Page"

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage"]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 1200},
            )
            page = await context.new_page()

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=35000)
                await page.wait_for_timeout(5000)   # صبر برای JS

                if "x.com" in url.lower() or "twitter.com" in url.lower():
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight/2)")
                    await page.wait_for_timeout(1500)

                html_content = await page.content()
                title = await page.title() or title
                post_meta["title"] = title

            except PlaywrightTimeout:
                logger.warning("Timeout HTML — محتوای موجود ذخیره شد")
                html_content = await page.content()
            except Exception as e:
                logger.error(f"HTML error: {e}")
                html_content = f"<h1>خطا در آرشیو</h1><p>{e}</p>"
            finally:
                await browser.close()

        # ── ۲. اسکرین‌شات از thum.io (سریع و بدون crash) ──
        screenshot_bytes = await _get_screenshot(url)
        screenshot_path.write_bytes(screenshot_bytes)

        # ── ۳. بنر Archive Hub مثل archive.is ──
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
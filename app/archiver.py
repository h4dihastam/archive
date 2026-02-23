"""
Archiver — آرشیو کامل صفحه با Playwright (Docker روی Render)
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse, quote

import httpx

from app.config import settings
from app.models import ArchiveArtifact

logger = logging.getLogger(__name__)


def _safe_slug(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace(":", "_").replace(".", "_")
    path = parsed.path.strip("/").replace("/", "_") or "page"
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return (host + "_" + path + "_" + ts)[:100]


async def _screenshot_fallback(url: str) -> bytes:
    """اگه Playwright fail شد، از screenshotmachine بگیر"""
    try:
        key = settings.screenshot_machine_key
        encoded = quote(url, safe="")
        sm_url = "https://api.screenshotmachine.com/?key=" + key + "&url=" + encoded + "&dimension=1280x900&format=png&delay=4000"
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.get(sm_url)
            if r.status_code == 200 and r.headers.get("content-type","").startswith("image"):
                return r.content
    except Exception as e:
        logger.warning("screenshot fallback failed: %s", e)
    return b""


class Archiver:
    async def archive(self, url: str) -> ArchiveArtifact:
        slug = _safe_slug(url)
        folder = Path(settings.base_storage_dir) / slug
        folder.mkdir(parents=True, exist_ok=True)

        raw_html_path = folder / "raw.html"
        rendered_html_path = folder / "archive.html"
        screenshot_path = folder / "screenshot.png"
        post_meta = {}

        try:
            from playwright.async_api import async_playwright, TimeoutError as PWTimeout

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox",
                          "--disable-dev-shm-usage", "--disable-gpu"]
                )
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                )
                page = await context.new_page()

                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    await page.wait_for_timeout(5000)

                    # اسکرول برای لود محتوای lazy
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
                    await page.wait_for_timeout(2000)

                    html = await page.content()
                    title = await page.title() or url
                    post_meta["title"] = title

                    # username از title توییتر
                    um = re.search(r'\(@([^)]+)\)', title)
                    if um:
                        post_meta["username"] = um.group(1)

                    raw_html_path.write_text(html, encoding="utf-8")

                    # بنر archive.is-style
                    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
                    banner = (
                        '<div style="position:fixed;top:0;left:0;right:0;z-index:2147483647;'
                        'background:#1e40af;color:#fff;padding:10px 20px;font-family:system-ui,sans-serif;'
                        'display:flex;align-items:center;gap:12px;box-shadow:0 2px 8px rgba(0,0,0,.4);font-size:13px;">'
                        '📦 <strong>Archive Hub</strong>'
                        '<span>' + now_str + '</span>'
                        '<a href="' + url + '" target="_blank" style="color:#93c5fd;margin-left:auto;">🔗 لینک اصلی</a>'
                        '</div>'
                        '<script>document.documentElement.style.paddingTop="50px";</script>'
                    )
                    archived = html.replace("</body>", banner + "</body>", 1) if "</body>" in html else html + banner
                    rendered_html_path.write_text(archived, encoding="utf-8")

                    # اسکرین‌شات
                    await page.screenshot(path=str(screenshot_path), full_page=False)
                    logger.info("Playwright archive done: %s", url)

                except PWTimeout:
                    logger.warning("Playwright timeout: %s", url)
                    html = "<h2>Timeout</h2><p>" + url + "</p>"
                    rendered_html_path.write_text(html, encoding="utf-8")
                    raw_html_path.write_text(html, encoding="utf-8")
                except Exception as e:
                    logger.error("Playwright page error: %s", e)
                    html = "<h2>Error</h2><p>" + url + "</p><p>" + str(e) + "</p>"
                    rendered_html_path.write_text(html, encoding="utf-8")
                    raw_html_path.write_text(html, encoding="utf-8")
                finally:
                    await browser.close()

        except ImportError:
            # Playwright نصب نیست — fallback به httpx + screenshotmachine
            logger.warning("Playwright not available, using httpx fallback")
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 Chrome/122.0.0.0"}) as c:
                    r = await c.get(url)
                    html = r.text
                    raw_html_path.write_text(html, encoding="utf-8")
                    rendered_html_path.write_text(html, encoding="utf-8")
            except Exception as e:
                rendered_html_path.write_text("<p>Error: " + str(e) + "</p>", encoding="utf-8")
                raw_html_path.write_text("", encoding="utf-8")

            # screenshot از screenshotmachine
            ss = await _screenshot_fallback(url)
            screenshot_path.write_bytes(ss)

        except Exception as e:
            logger.error("Archiver error: %s", e)
            rendered_html_path.write_text("<p>Error: " + str(e) + "</p>", encoding="utf-8")
            raw_html_path.write_text("", encoding="utf-8")
            screenshot_path.write_bytes(b"")

        # اگه screenshot نگرفت، از fallback استفاده کن
        if not screenshot_path.exists() or screenshot_path.stat().st_size < 1000:
            ss = await _screenshot_fallback(url)
            screenshot_path.write_bytes(ss)

        return ArchiveArtifact(
            url=url,
            created_at=datetime.now(UTC),
            folder=folder,
            raw_html_path=raw_html_path,
            rendered_html_path=rendered_html_path,
            screenshot_path=screenshot_path,
            post_meta=post_meta,
        )

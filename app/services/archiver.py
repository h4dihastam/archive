"""
Archiver — Playwright برای HTML کامل + screenshot
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


async def _screenshot_api(url: str) -> bytes:
    """Screenshot از screenshotmachine به عنوان fallback"""
    key = settings.screenshot_machine_key or "dd29ad"
    encoded = quote(url, safe="")
    sm_url = (
        "https://api.screenshotmachine.com/"
        "?key=" + key +
        "&url=" + encoded +
        "&dimension=1280x900&format=png&delay=4000"
    )
    try:
        async with httpx.AsyncClient(timeout=45) as c:
            r = await c.get(sm_url)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and ct.startswith("image") and len(r.content) > 5000:
                return r.content
    except Exception as e:
        logger.warning("screenshotmachine failed: %s", e)
    return b""


async def _get_microlink(url: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.get(
                "https://api.microlink.io/",
                params={"url": url, "meta": "true"},
            )
            if r.status_code == 200:
                return r.json().get("data", {})
    except Exception as e:
        logger.warning("microlink failed: %s", e)
    return {}


class Archiver:
    async def archive(self, url: str) -> ArchiveArtifact:
        slug = _safe_slug(url)
        folder = Path(settings.base_storage_dir) / slug
        folder.mkdir(parents=True, exist_ok=True)

        raw_html_path = folder / "raw.html"
        rendered_html_path = folder / "archive.html"
        screenshot_path = folder / "screenshot.png"
        post_meta: dict = {}

        html_content = ""
        screenshot_bytes = b""

        # ── Playwright ────────────────────────────────────────────────────
        try:
            from playwright.async_api import async_playwright

            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-web-security",
                    ]
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

                    # اسکرول برای لود lazy content
                    await page.evaluate("window.scrollTo(0, 300)")
                    await page.wait_for_timeout(2000)

                    title = await page.title() or url
                    html_content = await page.content()

                    # screenshot
                    ss = await page.screenshot(full_page=False)
                    screenshot_bytes = ss

                    # username از title توییتر
                    post_meta["title"] = title
                    um = re.search(r'\(@([^)]+)\)', title)
                    if um:
                        post_meta["username"] = um.group(1)

                    logger.info("Playwright OK: %s (%d bytes html)", url, len(html_content))

                except Exception as e:
                    logger.error("Playwright page error: %s", e)
                    html_content = "<h2>خطا در آرشیو</h2><p>" + url + "</p><p>" + str(e) + "</p>"

                finally:
                    await browser.close()

        except ImportError:
            logger.warning("Playwright not installed — using httpx fallback")
            # fallback: httpx + microlink
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 Chrome/122.0.0.0"}) as c:
                    r = await c.get(url)
                    html_content = r.text
            except Exception as e:
                html_content = "<p>Error: " + str(e) + "</p>"

            meta = await _get_microlink(url)
            post_meta = {
                "title": meta.get("title", ""),
                "author": meta.get("author", ""),
                "username": "",
                "date": meta.get("date", ""),
            }
            um = re.search(r'\(@([^)]+)\)', post_meta.get("title", ""))
            if um:
                post_meta["username"] = um.group(1)

        except Exception as e:
            logger.error("Playwright launch error: %s", e)
            html_content = "<h2>خطا</h2><p>" + str(e) + "</p>"

        # ── Screenshot fallback ───────────────────────────────────────────
        if not screenshot_bytes or len(screenshot_bytes) < 1000:
            logger.info("Using screenshot API fallback")
            screenshot_bytes = await _screenshot_api(url)

        # ── بنر Archive Hub به HTML اضافه کن ─────────────────────────────
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        banner = (
            '<div id="__archive_banner__" style="position:fixed;top:0;left:0;right:0;'
            'z-index:2147483647;background:#1e40af;color:#fff;padding:10px 20px;'
            'font-family:system-ui,sans-serif;display:flex;align-items:center;gap:12px;'
            'box-shadow:0 2px 8px rgba(0,0,0,.4);font-size:13px;">'
            '📦 <strong>Archive Hub</strong>'
            '<span>' + now_str + '</span>'
            '<a href="' + url + '" target="_blank" style="color:#93c5fd;margin-right:auto;text-decoration:none;">'
            '🔗 لینک اصلی</a>'
            '</div>'
            '<style>#__archive_banner__~* { margin-top: 50px !important; }</style>'
        )

        if "</body>" in html_content:
            archived_html = html_content.replace("</body>", banner + "</body>", 1)
        else:
            archived_html = banner + html_content

        # ── ذخیره فایل‌ها ──────────────────────────────────────────────
        raw_html_path.write_text(html_content, encoding="utf-8")
        rendered_html_path.write_text(archived_html, encoding="utf-8")
        screenshot_path.write_bytes(screenshot_bytes)

        return ArchiveArtifact(
            url=url,
            created_at=datetime.now(UTC),
            folder=folder,
            raw_html_path=raw_html_path,
            rendered_html_path=rendered_html_path,
            screenshot_path=screenshot_path,
            post_meta=post_meta,
        )

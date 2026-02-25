"""
Archiver — Playwright با X cookie + screenshotmachine fallback
"""
from __future__ import annotations

import json
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


def _is_twitter(url: str) -> bool:
    return "x.com" in url.lower() or "twitter.com" in url.lower()


def _get_x_cookies() -> list[dict]:
    """کوکی‌های X.com از env"""
    raw = (settings.x_cookies or "").strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception as e:
        logger.warning("Failed to parse X_COOKIES: %s", e)
        return []


async def _screenshot_api(url: str) -> bytes:
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


def _add_banner(html: str, url: str) -> str:
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    banner = (
        '<div id="__archive_banner__" style="position:fixed;top:0;left:0;right:0;'
        'z-index:2147483647;background:#1e40af;color:#fff;padding:10px 20px;'
        'font-family:system-ui,sans-serif;display:flex;align-items:center;gap:12px;'
        'box-shadow:0 2px 8px rgba(0,0,0,.4);font-size:13px;direction:rtl;">'
        '📦 <strong>Archive Hub</strong>'
        '<span style="color:#bfdbfe;">' + now_str + '</span>'
        '<a href="' + url + '" target="_blank" '
        'style="color:#93c5fd;margin-right:auto;text-decoration:none;">🔗 لینک اصلی</a>'
        '</div>'
        '<style>body{padding-top:50px!important;}</style>'
    )
    if "</body>" in html:
        return html.replace("</body>", banner + "</body>", 1)
    return banner + html


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
        is_twitter = _is_twitter(url)

        # ── Playwright ──────────────────────────────────────────────────
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
                    ]
                )

                context_args = dict(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 900},
                    locale="en-US",
                )
                context = await browser.new_context(**context_args)

                # ── اضافه کردن کوکی X.com ─────────────────────────────
                if is_twitter:
                    cookies = _get_x_cookies()
                    if cookies:
                        # فرمت Playwright نیاز به url یا domain داره
                        pw_cookies = []
                        for ck in cookies:
                            entry = {
                                "name": ck["name"],
                                "value": ck["value"],
                                "domain": ck.get("domain", ".x.com"),
                                "path": ck.get("path", "/"),
                                "secure": ck.get("secure", True),
                                "httpOnly": ck.get("httpOnly", False),
                                "sameSite": ck.get("sameSite", "None") or "None",
                            }
                            pw_cookies.append(entry)
                        await context.add_cookies(pw_cookies)
                        logger.info("Added %d X.com cookies", len(pw_cookies))
                    else:
                        logger.warning("No X_COOKIES found in env!")

                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="networkidle", timeout=40000)
                    await page.wait_for_timeout(4000)

                    # اسکرول برای لود lazy content
                    await page.evaluate("window.scrollTo(0, document.body.scrollHeight/3)")
                    await page.wait_for_timeout(2000)

                    title = await page.title() or url
                    html_content = await page.content()
                    screenshot_bytes = await page.screenshot(full_page=False, type="png")

                    post_meta["title"] = title
                    um = re.search(r'\(@([^)]+)\)', title)
                    if um:
                        post_meta["username"] = um.group(1)

                    logger.info("Playwright OK: %s — %d bytes html", url, len(html_content))

                except Exception as e:
                    logger.error("Playwright page error: %s", e)
                    html_content = "<h2>خطا در آرشیو</h2><p>" + str(e) + "</p>"
                finally:
                    await browser.close()

        except ImportError:
            logger.warning("Playwright not installed — httpx fallback")
            try:
                async with httpx.AsyncClient(
                    timeout=20, follow_redirects=True,
                    headers={"User-Agent": "Mozilla/5.0 Chrome/122.0.0.0"}
                ) as c:
                    r = await c.get(url)
                    html_content = r.text
            except Exception as e:
                html_content = "<p>Error: " + str(e) + "</p>"

        except Exception as e:
            logger.error("Playwright launch error: %s", e)
            html_content = "<h2>خطا</h2><p>" + str(e) + "</p>"

        # ── Screenshot fallback ─────────────────────────────────────────
        if not screenshot_bytes or len(screenshot_bytes) < 2000:
            logger.info("Falling back to screenshotmachine")
            screenshot_bytes = await _screenshot_api(url)

        # ── بنر + ذخیره ────────────────────────────────────────────────
        archived_html = _add_banner(html_content, url)
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

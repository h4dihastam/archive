"""
Archiver نهایی — X.com با oEmbed + Microlink + thum.io screenshot
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

BLOCKED = ["this page doesn't exist", "page not found", "something went wrong",
           "hmm...", "not available", "sign in to x", "log in to twitter"]


def _safe_slug(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace(":", "_").replace(".", "_")
    path = parsed.path.strip("/").replace("/", "_") or "page"
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return (host + "_" + path + "_" + ts)[:100]


def _is_twitter(url: str) -> bool:
    return "x.com" in url.lower() or "twitter.com" in url.lower()


def _is_blocked(html: str) -> bool:
    low = html.lower()
    return any(kw in low for kw in BLOCKED)


def _get_x_cookies() -> list[dict]:
    raw = (settings.x_cookies or "").strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except Exception as e:
        logger.warning("X_COOKIES parse error: %s", e)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Screenshot — thum.io (رایگان، بدون محدودیت)
# ─────────────────────────────────────────────────────────────────────────────
async def _screenshot(url: str) -> bytes:
    encoded = quote(url, safe="")
    candidates = [
        f"https://image.thum.io/get/width/1280/crop/900/noanimate/allowJPG/{encoded}",
        f"https://api.screenshotmachine.com/?key={settings.screenshot_machine_key or 'dd29ad'}&url={encoded}&dimension=1366x768&format=png&delay=4000",
        f"https://image.thum.io/get/width/1280/noanimate/{encoded}",
    ]
    async with httpx.AsyncClient(timeout=35, follow_redirects=True) as c:
        for ss_url in candidates:
            try:
                r = await c.get(ss_url)
                ct = r.headers.get("content-type", "")
                if r.status_code == 200 and "image" in ct and len(r.content) > 8_000:
                    logger.info("screenshot OK: %d bytes from %s", len(r.content), ss_url[:50])
                    return r.content
            except Exception as e:
                logger.warning("screenshot candidate failed: %s", e)
    return b""


# ─────────────────────────────────────────────────────────────────────────────
# X.com محتوا — oEmbed + Microlink (بدون Playwright)
# ─────────────────────────────────────────────────────────────────────────────
async def _fetch_x_content(url: str) -> dict:
    """
    برمی‌گردونه: {html, title, author, text, media_url}
    """
    result = {"html": "", "title": "", "author": "", "text": "", "media_url": ""}

    # ۱. Twitter oEmbed API — رایگان و بدون auth
    try:
        oembed_url = f"https://publish.twitter.com/oembed?url={quote(url)}&dnt=true&omit_script=true"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(oembed_url)
            if r.status_code == 200:
                data = r.json()
                result["html"] = data.get("html", "")
                result["author"] = data.get("author_name", "")
                result["title"] = f"پست از {data.get('author_name', '')}"
                logger.info("oEmbed OK: author=%s", result["author"])
                return result
    except Exception as e:
        logger.warning("oEmbed failed: %s", e)

    # ۲. Microlink API — رایگان ۱۰۰۰ req/day
    try:
        ml_url = f"https://api.microlink.io/?url={quote(url)}&meta=true&screenshot=false"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(ml_url)
            if r.status_code == 200:
                data = r.json().get("data", {})
                result["title"] = data.get("title", "")
                result["text"] = data.get("description", "")
                result["author"] = data.get("author", "")
                if data.get("image", {}).get("url"):
                    result["media_url"] = data["image"]["url"]
                logger.info("Microlink OK: title=%s", result["title"][:60])
                return result
    except Exception as e:
        logger.warning("Microlink failed: %s", e)

    return result


def _build_x_html(url: str, data: dict) -> str:
    """HTML زیبا برای توییت وقتی Playwright بلاک شد"""
    oembed_html = data.get("html", "")
    author = data.get("author", "")
    title = data.get("title", "پست آرشیو شده")
    text = data.get("text", "")
    media_url = data.get("media_url", "")
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    media_section = f'<img src="{media_url}" style="width:100%;border-radius:12px;margin-top:16px;" alt="media"/>' if media_url else ""

    if oembed_html:
        # oEmbed HTML کامله — فقط wrap می‌کنیم
        content_section = f"""
        <div style="display:flex;justify-content:center;">
          <div style="max-width:550px;width:100%;">
            {oembed_html}
            <script async src="https://platform.twitter.com/widgets.js" charset="utf-8"></script>
          </div>
        </div>"""
    else:
        content_section = f"""
        <div class="tweet-card">
          {f'<div class="author">👤 {author}</div>' if author else ''}
          {f'<p class="tweet-text">{text}</p>' if text else ''}
          {media_section}
          <a href="{url}" target="_blank" class="orig-link">🔗 مشاهده پست اصلی در X.com</a>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title} — Archive Hub</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;
  min-height:100vh;padding:80px 16px 40px;}}
.banner{{position:fixed;top:0;left:0;right:0;z-index:9999;background:#1e40af;
  color:#fff;padding:12px 20px;display:flex;align-items:center;gap:12px;
  box-shadow:0 2px 12px rgba(0,0,0,.5);font-size:14px;flex-wrap:wrap;}}
.banner strong{{white-space:nowrap;}}
.banner .date{{color:#bfdbfe;font-size:12px;}}
.banner a{{color:#93c5fd;text-decoration:none;margin-right:auto;font-size:12px;}}
.tweet-card{{max-width:600px;margin:0 auto;background:#1e293b;
  border-radius:16px;padding:28px;box-shadow:0 8px 32px rgba(0,0,0,.4);}}
.author{{color:#60a5fa;font-size:1rem;margin-bottom:14px;}}
.tweet-text{{font-size:1.1rem;line-height:1.7;color:#e2e8f0;margin-bottom:20px;}}
.orig-link{{display:inline-block;margin-top:20px;padding:10px 24px;
  background:#1d4ed8;color:#fff;border-radius:10px;text-decoration:none;
  font-weight:600;font-size:.9rem;}}
.orig-link:hover{{background:#2563eb;}}
.archive-note{{max-width:600px;margin:20px auto 0;padding:12px 16px;
  background:rgba(99,102,241,.1);border:1px solid rgba(99,102,241,.25);
  border-radius:10px;font-size:.8rem;color:#94a3b8;text-align:center;}}
</style>
</head>
<body>
<div class="banner">
  <strong>📦 Archive Hub</strong>
  <span class="date">{now_str}</span>
  <a href="{url}" target="_blank">🔗 لینک اصلی ↗</a>
</div>
{content_section}
<div class="archive-note">
  این صفحه توسط Archive Hub آرشیو شده است — {now_str}
</div>
</body>
</html>"""


def _add_banner(html: str, url: str) -> str:
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    banner = (
        f'<div style="position:fixed;top:0;left:0;right:0;z-index:2147483647;'
        f'background:#1e40af;color:#fff;padding:10px 20px;font-family:system-ui,sans-serif;'
        f'display:flex;align-items:center;gap:12px;box-shadow:0 2px 8px rgba(0,0,0,.4);font-size:13px;">'
        f'📦 <strong>Archive Hub</strong>'
        f'<span style="color:#bfdbfe;font-size:12px;">{now_str}</span>'
        f'<a href="{url}" target="_blank" style="color:#93c5fd;margin-right:auto;'
        f'text-decoration:none;font-size:12px;">🔗 لینک اصلی</a>'
        f'</div>'
        f'<style>body{{padding-top:50px!important;}}</style>'
    )
    if "</body>" in html:
        return html.replace("</body>", banner + "</body>", 1)
    return banner + html


# ─────────────────────────────────────────────────────────────────────────────
# HTML با Playwright (برای سایت‌های غیر X)
# ─────────────────────────────────────────────────────────────────────────────
async def _playwright_html(url: str, use_x_cookies: bool = False) -> str:
    try:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox",
                      "--disable-dev-shm-usage", "--disable-gpu",
                      "--disable-blink-features=AutomationControlled"]
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )

            await context.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )

            if use_x_cookies:
                cookies = _get_x_cookies()
                if cookies:
                    pw_cookies = [{
                        "name": ck["name"], "value": ck["value"],
                        "domain": ck.get("domain", ".x.com"),
                        "path": ck.get("path", "/"),
                        "secure": ck.get("secure", True),
                        "httpOnly": ck.get("httpOnly", False),
                        "sameSite": ck.get("sameSite", "None") or "None",
                    } for ck in cookies]
                    await context.add_cookies(pw_cookies)
                    logger.info("Added %d X cookies", len(pw_cookies))

            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=35000)
                await page.wait_for_timeout(5000)
                html = await page.content()
                return html
            except Exception as e:
                logger.warning("Playwright page error: %s", e)
                return ""
            finally:
                await browser.close()
    except ImportError:
        logger.warning("Playwright not installed")
    except Exception as e:
        logger.error("Playwright launch error: %s", e)
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Archiver اصلی
# ─────────────────────────────────────────────────────────────────────────────
class Archiver:
    async def archive(self, url: str) -> ArchiveArtifact:
        slug = _safe_slug(url)
        folder = Path(settings.base_storage_dir) / slug
        folder.mkdir(parents=True, exist_ok=True)

        raw_html_path = folder / "raw.html"
        rendered_html_path = folder / "archive.html"
        screenshot_path = folder / "screenshot.png"
        post_meta: dict = {}
        is_twitter = _is_twitter(url)

        # ── Screenshot — همیشه اول (سریع‌تره) ──────────────────────────
        screenshot_bytes = await _screenshot(url)
        screenshot_path.write_bytes(screenshot_bytes)

        # ── HTML ────────────────────────────────────────────────────────
        if is_twitter:
            # اول Playwright با کوکی امتحان کن
            has_cookies = bool(_get_x_cookies())
            html_content = ""

            if has_cookies:
                html_content = await _playwright_html(url, use_x_cookies=True)
                if _is_blocked(html_content) or len(html_content) < 3000:
                    logger.warning("X.com blocked Playwright (with cookies) → using API fallback")
                    html_content = ""

            if not html_content:
                # API fallback — همیشه کار می‌کنه
                x_data = await _fetch_x_content(url)
                post_meta["author"] = x_data.get("author", "")
                post_meta["title"] = x_data.get("title", "")
                html_content = _build_x_html(url, x_data)

            raw_html = html_content
            rendered_html = html_content  # بنر داخل _build_x_html هست

        else:
            # سایت‌های دیگه — Playwright معمولی
            html_content = await _playwright_html(url)
            if not html_content or len(html_content) < 500:
                try:
                    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                        headers={"User-Agent": "Mozilla/5.0 Chrome/122.0.0.0"}) as c:
                        r = await c.get(url)
                        html_content = r.text
                except Exception as e:
                    html_content = f"<h2>خطا</h2><p>{url}</p><p>{e}</p>"

            raw_html = html_content
            rendered_html = _add_banner(html_content, url)

        # ── ذخیره ──────────────────────────────────────────────────────
        raw_html_path.write_text(raw_html, encoding="utf-8")
        rendered_html_path.write_text(rendered_html, encoding="utf-8")

        logger.info("Archive done: html=%d ss=%d", len(rendered_html), len(screenshot_bytes))

        return ArchiveArtifact(
            url=url,
            created_at=datetime.now(UTC),
            folder=folder,
            raw_html_path=raw_html_path,
            rendered_html_path=rendered_html_path,
            screenshot_path=screenshot_path,
            post_meta=post_meta,
        )

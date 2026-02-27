"""
Archiver نهایی — X.com با oEmbed inline + Microlink + thum.io
محتوا کامل inline ذخیره میشه — حتی اگه پست پاک بشه
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
# Screenshot
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
                    logger.info("screenshot OK: %d bytes", len(r.content))
                    return r.content
            except Exception as e:
                logger.warning("screenshot candidate failed: %s", e)
    return b""


# ─────────────────────────────────────────────────────────────────────────────
# X.com — دریافت محتوای کامل
# ─────────────────────────────────────────────────────────────────────────────
async def _fetch_x_content(url: str) -> dict:
    """
    محتوا رو از چند API می‌گیره و inline ذخیره می‌کنه
    برمی‌گردونه: {author, text, date, media_urls, tweet_id, found}
    """
    result = {
        "found": False,
        "author": "",
        "author_handle": "",
        "text": "",
        "date": "",
        "media_urls": [],
        "tweet_id": "",
        "profile_image": "",
    }

    # tweet ID از URL
    m = re.search(r'/status/(\d+)', url)
    if m:
        result["tweet_id"] = m.group(1)

    # ── ۱. Twitter oEmbed — متن و اطلاعات نویسنده ─────────────────────
    try:
        oembed_url = f"https://publish.twitter.com/oembed?url={quote(url)}&dnt=true&omit_script=true"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(oembed_url)
            if r.status_code == 200:
                data = r.json()
                raw_html = data.get("html", "")
                result["author"] = data.get("author_name", "")
                result["author_handle"] = data.get("author_url", "").split("/")[-1] if data.get("author_url") else ""

                # استخراج متن از blockquote
                text_match = re.search(r'<blockquote[^>]*>\s*<p[^>]*>(.*?)</p>', raw_html, re.DOTALL)
                if text_match:
                    raw_text = text_match.group(1)
                    # پاک کردن تگ‌های HTML
                    result["text"] = re.sub(r'<[^>]+>', '', raw_text).strip()

                # تاریخ
                date_match = re.search(r'<a[^>]+>([A-Za-z]+ \d+, \d+)</a>', raw_html)
                if date_match:
                    result["date"] = date_match.group(1)

                result["found"] = True
                logger.info("oEmbed OK: @%s — %s", result["author_handle"], result["text"][:50])
    except Exception as e:
        logger.warning("oEmbed failed: %s", e)

    # ── ۲. Microlink — تصاویر و اطلاعات بیشتر ────────────────────────
    try:
        ml_url = f"https://api.microlink.io/?url={quote(url)}&meta=true&screenshot=false"
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as c:
            r = await c.get(ml_url)
            if r.status_code == 200:
                data = r.json().get("data", {})

                if not result["text"] and data.get("description"):
                    result["text"] = data["description"]
                if not result["author"] and data.get("author"):
                    result["author"] = data["author"]
                if not result["date"] and data.get("date"):
                    result["date"] = data["date"][:10]

                # تصاویر
                img = data.get("image", {})
                if img and img.get("url"):
                    result["media_urls"].append(img["url"])

                if not result["found"] and (result["text"] or result["author"]):
                    result["found"] = True

                logger.info("Microlink OK: %s", data.get("title", "")[:60])
    except Exception as e:
        logger.warning("Microlink failed: %s", e)

    return result


def _build_x_html(url: str, data: dict) -> str:
    """
    HTML کامل inline — همه محتوا داخل HTML ذخیره میشه
    وقتی پست پاک بشه هم نشون میده
    """
    author = data.get("author", "ناشناس")
    handle = data.get("author_handle", "")
    text = data.get("text", "")
    date = data.get("date", "")
    media_urls = data.get("media_urls", [])
    tweet_id = data.get("tweet_id", "")
    found = data.get("found", False)
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    # نمایش زمان آرشیو
    archive_time = datetime.now(UTC).strftime("%d %B %Y — %H:%M UTC")

    # اگه اصلاً چیزی پیدا نشد
    if not found or not text:
        status_html = f"""
        <div class="not-found-note">
          ⚠️ محتوای این پست در زمان آرشیو در دسترس نبود یا پاک شده بود.<br/>
          <a href="{url}" target="_blank" style="color:#60a5fa;">🔗 تلاش برای دیدن پست اصلی</a>
        </div>"""
    else:
        status_html = ""

    # تصاویر
    media_html = ""
    for img_url in media_urls[:4]:
        media_html += f'<img src="{img_url}" class="media-img" alt="media" onerror="this.style.display=\'none\'"/>'

    # متن پست
    text_escaped = text.replace("<", "&lt;").replace(">", "&gt;")
    # لینک‌های توییتر آبی
    text_linked = re.sub(r'(https?://\S+)', r'<a href="\1" target="_blank" style="color:#60a5fa;">\1</a>', text_escaped)
    text_linked = re.sub(r'(@\w+)', r'<a href="https://x.com/\1" target="_blank" style="color:#60a5fa;">\1</a>', text_linked)
    text_linked = re.sub(r'(#\w+)', r'<a href="https://x.com/hashtag/\1" target="_blank" style="color:#60a5fa;">\1</a>', text_linked)

    handle_html = f'<span class="handle">@{handle}</span>' if handle else ""
    date_html = f'<span class="date">📅 {date}</span>' if date else ""
    id_html = f'<span class="tweet-id">ID: {tweet_id}</span>' if tweet_id else ""

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>پست {author} — Archive Hub</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;
  color:#e2e8f0;min-height:100vh;padding:72px 16px 48px;}}
.banner{{position:fixed;top:0;left:0;right:0;z-index:9999;background:#1e40af;
  color:#fff;padding:11px 20px;display:flex;align-items:center;gap:10px;
  box-shadow:0 2px 12px rgba(0,0,0,.5);font-size:13px;flex-wrap:wrap;}}
.banner strong{{white-space:nowrap;}}
.banner .bdate{{color:#bfdbfe;font-size:11px;}}
.banner a{{color:#93c5fd;text-decoration:none;margin-right:auto;font-size:11px;}}
.container{{max-width:620px;margin:0 auto;}}
.tweet-card{{background:#1e293b;border-radius:16px;padding:24px;
  box-shadow:0 8px 32px rgba(0,0,0,.4);border:1px solid rgba(99,102,241,.15);}}
.tweet-header{{display:flex;align-items:flex-start;gap:12px;margin-bottom:16px;}}
.avatar{{width:48px;height:48px;border-radius:50%;background:#334155;
  display:flex;align-items:center;justify-content:center;font-size:20px;flex-shrink:0;}}
.author-info .name{{font-weight:700;font-size:1rem;color:#f1f5f9;}}
.handle{{color:#64748b;font-size:.88rem;}}
.verified{{color:#1d9bf0;margin-right:4px;}}
.tweet-text{{font-size:1rem;line-height:1.7;color:#e2e8f0;margin-bottom:16px;
  white-space:pre-wrap;word-break:break-word;}}
.media-img{{width:100%;border-radius:12px;margin-top:12px;display:block;
  max-height:500px;object-fit:cover;}}
.tweet-footer{{margin-top:16px;padding-top:14px;border-top:1px solid rgba(255,255,255,.06);
  display:flex;flex-wrap:wrap;gap:8px;align-items:center;}}
.date{{color:#64748b;font-size:.82rem;}}
.tweet-id{{color:#475569;font-size:.75rem;}}
.orig-link{{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;
  background:rgba(29,155,240,.15);border:1px solid rgba(29,155,240,.3);
  color:#38bdf8;border-radius:20px;text-decoration:none;font-size:.85rem;
  font-weight:600;margin-right:auto;transition:.2s;}}
.orig-link:hover{{background:rgba(29,155,240,.25);}}
.archive-badge{{margin-top:16px;padding:10px 14px;
  background:rgba(99,102,241,.08);border:1px solid rgba(99,102,241,.18);
  border-radius:10px;font-size:.75rem;color:#94a3b8;text-align:center;}}
.not-found-note{{background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.2);
  border-radius:12px;padding:16px;text-align:center;color:#fca5a5;font-size:.9rem;
  margin-bottom:16px;line-height:1.7;}}
</style>
</head>
<body>
<div class="banner">
  <strong>📦 Archive Hub</strong>
  <span class="bdate">{now_str}</span>
  <a href="{url}" target="_blank">🔗 لینک اصلی ↗</a>
</div>

<div class="container">
  {status_html}
  <div class="tweet-card">
    <div class="tweet-header">
      <div class="avatar">🐦</div>
      <div class="author-info">
        <div class="name">{author} <span class="verified">✓</span></div>
        {handle_html}
      </div>
    </div>

    {"<p class='tweet-text'>" + text_linked + "</p>" if text_linked else ""}
    {media_html}

    <div class="tweet-footer">
      {date_html}
      {id_html}
      <a href="{url}" target="_blank" class="orig-link">
        𝕏 مشاهده در X.com
      </a>
    </div>
  </div>

  <div class="archive-badge">
    🗄 آرشیو شده توسط Archive Hub — {archive_time}<br/>
    این محتوا به صورت offline ذخیره شده است
  </div>
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
                return await page.content()
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

        # ── Screenshot ─────────────────────────────────────────────────
        screenshot_bytes = await _screenshot(url)
        screenshot_path.write_bytes(screenshot_bytes)

        # ── HTML ───────────────────────────────────────────────────────
        if is_twitter:
            # اول کوکی امتحان
            has_cookies = bool(_get_x_cookies())
            playwright_html = ""

            if has_cookies:
                playwright_html = await _playwright_html(url, use_x_cookies=True)
                if _is_blocked(playwright_html) or len(playwright_html) < 3000:
                    logger.warning("Playwright blocked → API fallback")
                    playwright_html = ""

            if playwright_html:
                # Playwright موفق شد
                post_meta["title"] = re.search(r'<title>(.*?)</title>', playwright_html, re.IGNORECASE)
                post_meta["title"] = post_meta["title"].group(1) if post_meta.get("title") else ""
                raw_html = playwright_html
                rendered_html = _add_banner(playwright_html, url)
            else:
                # API fallback — محتوا inline ذخیره میشه
                x_data = await _fetch_x_content(url)
                post_meta["author"] = x_data.get("author", "")
                post_meta["title"] = f"پست {x_data.get('author', '')} — {x_data.get('text', '')[:60]}"
                rendered_html = _build_x_html(url, x_data)
                raw_html = rendered_html

        else:
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

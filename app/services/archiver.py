"""
Archiver:
1. برای X.com: از Wayback Machine CDX API استفاده می‌کنه
2. اگه نبود: به save.org می‌فرسته تا آرشیو کنه
3. برای بقیه سایت‌ها: مستقیم fetch
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse, quote_plus

import httpx

from app.config import settings
from app.models import ArchiveArtifact

logger = logging.getLogger(__name__)

STEALTH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _safe_slug(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace(":", "_")
    path = parsed.path.strip("/").replace("/", "_") or "root"
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{host}_{path}_{ts}"


def _is_twitter(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(x in host for x in ["x.com", "twitter.com"])


def _make_archive_html(url: str, content: str, source: str = "") -> str:
    source_note = f"<small>منبع داده: {source}</small>" if source else ""
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Archive — {url}</title>
<style>
body{{font-family:Tahoma,sans-serif;background:#f0f4f8;margin:0;padding:0;}}
.banner{{background:#1d4ed8;color:#fff;padding:10px 16px;font-size:13px;}}
.banner a{{color:#93c5fd;}}
.content{{max-width:800px;margin:16px auto;background:#fff;border-radius:12px;
          padding:20px;box-shadow:0 2px 12px rgba(0,0,0,.08);}}
</style>
</head>
<body>
<div class="banner">
  📦 Archive Hub — <a href="{url}">{url}</a> {source_note}
</div>
<div class="content">{content}</div>
</body>
</html>"""


async def _try_wayback(url: str, client: httpx.AsyncClient) -> str | None:
    """آخرین snapshot از Wayback Machine CDX API بگیر"""
    try:
        cdx = (
            f"https://web.archive.org/cdx/search/cdx"
            f"?url={quote_plus(url)}&output=json&limit=1&fl=timestamp&filter=statuscode:200"
            f"&from=20200101&to=20991231"
        )
        r = await client.get(cdx, timeout=15)
        data = r.json()
        if len(data) < 2:
            return None
        ts = data[1][0]
        wayback_url = f"https://web.archive.org/web/{ts}/{url}"
        logger.info("Found wayback snapshot: %s", wayback_url)
        r2 = await client.get(wayback_url, timeout=20)
        if r2.status_code == 200:
            return r2.text
    except Exception as e:
        logger.warning("Wayback failed: %s", e)
    return None


async def _try_save_to_wayback(url: str, client: httpx.AsyncClient) -> str | None:
    """URL رو به Wayback Machine بفرست تا save کنه، لینک برگردون"""
    try:
        save_url = f"https://web.archive.org/save/{url}"
        r = await client.get(save_url, timeout=30)
        # Wayback Machine بعد از save به /web/timestamp/url redirect می‌کنه
        if r.url and "web.archive.org/web/" in str(r.url):
            return str(r.url)
        # یا از header Content-Location بگیر
        loc = r.headers.get("Content-Location", "")
        if loc:
            return f"https://web.archive.org{loc}"
    except Exception as e:
        logger.warning("Save to Wayback failed: %s", e)
    return None


async def _parse_tweet_from_wayback(html: str, original_url: str) -> str:
    """محتوای توییت رو از HTML Wayback Machine استخراج کن"""
    # پیدا کردن متن اصلی توییت
    patterns = [
        r'<div[^>]*data-testid="tweetText"[^>]*>(.*?)</div>',
        r'<p[^>]*class="[^"]*TweetText[^"]*"[^>]*>(.*?)</p>',
        r'"full_text"\s*:\s*"([^"]{10,})"',
    ]
    
    tweet_text = ""
    for pat in patterns:
        m = re.search(pat, html, re.DOTALL)
        if m:
            tweet_text = re.sub(r'<[^>]+>', ' ', m.group(1)).strip()
            tweet_text = re.sub(r'\s+', ' ', tweet_text)
            break

    # username
    user_m = re.search(r'"screen_name"\s*:\s*"([^"]+)"', html)
    username = f"@{user_m.group(1)}" if user_m else ""

    if tweet_text:
        return f"""
        <div style="border:1px solid #e5e7eb;border-radius:12px;padding:16px;max-width:550px;margin:0 auto;">
          <div style="font-weight:bold;margin-bottom:8px;">{username}</div>
          <div style="font-size:18px;line-height:1.6;">{tweet_text}</div>
          <div style="margin-top:12px;font-size:12px;color:#888;">
            <a href="{original_url}">{original_url}</a>
          </div>
        </div>"""
    else:
        return f'<p>محتوا استخراج نشد. <a href="{original_url}">لینک اصلی</a></p>'


class Archiver:
    async def archive(self, url: str) -> ArchiveArtifact:
        slug = _safe_slug(url)
        folder = Path(settings.base_storage_dir) / slug
        folder.mkdir(parents=True, exist_ok=True)

        raw_html_path = folder / "raw.html"
        rendered_html_path = folder / "archive.html"
        screenshot_path = folder / "screenshot.png"
        screenshot_path.write_bytes(b"")

        wayback_link = ""  # لینک Wayback Machine برای نمایش

        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers=STEALTH_HEADERS,
        ) as client:

            if _is_twitter(url):
                # ── استراتژی برای X.com ───────────────────────────────────

                # ۱. ابتدا از Wayback بگیر
                raw_html = await _try_wayback(url, client)

                if raw_html:
                    raw_html_path.write_text(raw_html, encoding="utf-8")
                    content = await _parse_tweet_from_wayback(raw_html, url)
                    archive_html = _make_archive_html(url, content, "Wayback Machine")
                    rendered_html_path.write_text(archive_html, encoding="utf-8")
                else:
                    # ۲. بفرست Wayback تا save کنه
                    logger.info("No wayback snapshot, saving now...")
                    wayback_link = await _try_save_to_wayback(url, client)
                    
                    if wayback_link:
                        # بعد از save دوباره fetch کن
                        try:
                            r = await client.get(wayback_link, timeout=20)
                            raw_html = r.text
                            raw_html_path.write_text(raw_html, encoding="utf-8")
                            content = await _parse_tweet_from_wayback(raw_html, url)
                        except Exception:
                            content = f'<p>آرشیو در Wayback Machine ذخیره شد.</p><p><a href="{wayback_link}">مشاهده در Wayback Machine</a></p>'
                    else:
                        content = f"""
                        <div style="padding:20px;text-align:center;">
                          <p>⚠️ محتوای پست در دسترس نبود.</p>
                          <p>لینک اصلی: <a href="{url}">{url}</a></p>
                          <p><a href="https://web.archive.org/web/*/{url}">جستجو در Wayback Machine</a></p>
                        </div>"""
                        raw_html_path.write_text("<!-- not available -->", encoding="utf-8")

                    archive_html = _make_archive_html(url, content, wayback_link or "")
                    rendered_html_path.write_text(archive_html, encoding="utf-8")

            else:
                # ── سایر سایت‌ها ─────────────────────────────────────────
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    raw_html_path.write_text(r.text, encoding="utf-8")
                    rendered_html_path.write_text(
                        _make_archive_html(url, r.text),
                        encoding="utf-8",
                    )
                except Exception as e:
                    logger.error("Fetch failed: %s", e)
                    raw_html_path.write_text(f"<!-- fetch failed: {e} -->", encoding="utf-8")
                    rendered_html_path.write_text(
                        _make_archive_html(url, f"<p>آرشیو ناموفق: {e}</p>"),
                        encoding="utf-8",
                    )

        return ArchiveArtifact(
            url=url,
            created_at=datetime.now(UTC),
            folder=folder,
            raw_html_path=raw_html_path,
            rendered_html_path=rendered_html_path,
            screenshot_path=screenshot_path,
        )

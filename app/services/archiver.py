"""
Archiver:
- Screenshot از thum.io (رایگان، بدون key)
- برای X.com: microlink.io برای metadata + محتوا
- برای بقیه: httpx مستقیم
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


async def _get_screenshot_bytes(url: str, client: httpx.AsyncClient) -> bytes:
    """
    screenshot از screenshotmachine.com
    بعد thum.io به عنوان fallback
    """
    encoded = quote(url, safe="")
    
    # ۱. screenshotmachine — برای سایت‌های عادی خوبه
    sm_key = settings.screenshot_machine_key
    if sm_key:
        try:
            sm_url = f"https://api.screenshotmachine.com/?key={sm_key}&url={encoded}&dimension=1280x900&format=png&delay=4000&hide=cookie-banners"
            r = await client.get(sm_url, timeout=45)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and ct.startswith("image") and len(r.content) > 5000:
                logger.info("Screenshot from screenshotmachine: %d bytes", len(r.content))
                return r.content
            else:
                logger.warning("screenshotmachine returned non-image or tiny: %s %d bytes", ct, len(r.content))
        except Exception as e:
            logger.warning("screenshotmachine failed: %s", e)

    # ۲. thum.io fallback
    try:
        thumb_url = f"https://image.thum.io/get/width/1280/crop/900/noanimate/allowJPG/{encoded}"
        r = await client.get(thumb_url, timeout=30)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image") and len(r.content) > 5000:
            logger.info("Screenshot from thum.io: %d bytes", len(r.content))
            return r.content
    except Exception as e:
        logger.warning("thum.io failed: %s", e)

    return b""


async def _get_microlink(url: str, client: httpx.AsyncClient) -> dict:
    """
    microlink.io — metadata + محتوای صفحه، رایگان
    """
    try:
        api_url = f"https://api.microlink.io/?url={quote(url, safe='')}&screenshot=false&meta=true"
        r = await client.get(api_url, timeout=20)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") == "success":
                return data.get("data", {})
    except Exception as e:
        logger.warning("microlink failed: %s", e)
    return {}


def _make_archive_html(url: str, content: str, title: str = "", author: str = "", date: str = "") -> str:
    meta = ""
    if author:
        meta += f'<div class="meta-item">👤 {author}</div>'
    if date:
        meta += f'<div class="meta-item">📅 {date}</div>'

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>{title or 'Archive'} — Archive Hub</title>
<style>
*{{box-sizing:border-box;}}
body{{font-family:Tahoma,Arial,sans-serif;background:#f0f4f8;margin:0;padding:0;color:#1a1a1a;}}
.banner{{background:#1d4ed8;color:#fff;padding:10px 20px;font-size:13px;display:flex;align-items:center;gap:8px;}}
.banner a{{color:#93c5fd;text-decoration:none;}}
.container{{max-width:680px;margin:20px auto;padding:0 16px;}}
.card{{background:#fff;border-radius:16px;padding:24px;box-shadow:0 2px 16px rgba(0,0,0,.08);}}
.title{{font-size:20px;font-weight:bold;margin-bottom:12px;line-height:1.4;}}
.meta{{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px;}}
.meta-item{{font-size:13px;color:#555;}}
.content{{font-size:16px;line-height:1.7;white-space:pre-wrap;word-break:break-word;}}
.source{{margin-top:20px;padding-top:16px;border-top:1px solid #eee;font-size:12px;color:#888;}}
.source a{{color:#3b82f6;}}
</style>
</head>
<body>
<div class="banner">
  📦 <span>Archive Hub</span>
  <a href="{url}" target="_blank">{url}</a>
</div>
<div class="container">
  <div class="card">
    {f'<div class="title">{title}</div>' if title else ''}
    {f'<div class="meta">{meta}</div>' if meta else ''}
    <div class="content">{content}</div>
    <div class="source">منبع: <a href="{url}" target="_blank">{url}</a></div>
  </div>
</div>
</body>
</html>"""


class Archiver:
    async def archive(self, url: str) -> ArchiveArtifact:
        slug = _safe_slug(url)
        folder = Path(settings.base_storage_dir) / slug
        folder.mkdir(parents=True, exist_ok=True)

        raw_html_path = folder / "raw.html"
        rendered_html_path = folder / "archive.html"
        screenshot_path = folder / "screenshot.png"

        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers=STEALTH_HEADERS,
        ) as client:

            # ── Screenshot از thum.io (برای همه سایت‌ها) ─────────────────────
            screenshot_bytes = await _get_screenshot_bytes(url, client)
            screenshot_path.write_bytes(screenshot_bytes)

            if _is_twitter(url):
                # ── X.com: از microlink محتوا بگیر ──────────────────────────
                meta = await _get_microlink(url, client)

                title = meta.get("title", "")
                description = meta.get("description", "")
                author = meta.get("author", "")
                publisher = meta.get("publisher", "")
                date = meta.get("date", "")

                # محتوای اصلی
                content = description or title or f"<p>محتوا استخراج نشد.</p>"
                
                # حذف HTML tags از description
                content = re.sub(r'<[^>]+>', '', content)

                # ساخت raw.html
                raw_data = f"URL: {url}\nTitle: {title}\nAuthor: {author}\nDate: {date}\nContent: {description}"
                raw_html_path.write_text(raw_data, encoding="utf-8")

                archive_html = _make_archive_html(
                    url=url,
                    content=content,
                    title=title,
                    author=f"{author} ({publisher})" if publisher and publisher != author else author,
                    date=date,
                )
                rendered_html_path.write_text(archive_html, encoding="utf-8")

            else:
                # ── سایر سایت‌ها: مستقیم fetch ──────────────────────────────
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    raw_html_path.write_text(r.text, encoding="utf-8")

                    # microlink برای metadata
                    meta = await _get_microlink(url, client)
                    title = meta.get("title", "")
                    description = meta.get("description", "")
                    author = meta.get("author", "")
                    date = meta.get("date", "")

                    if description:
                        content = re.sub(r'<[^>]+>', '', description)
                        archive_html = _make_archive_html(url, content, title, author, date)
                    else:
                        archive_html = (
                            f'<html><head><meta charset="UTF-8"/></head><body>'
                            f'<div style="background:#1d4ed8;color:#fff;padding:10px;">'
                            f'📦 <a href="{url}" style="color:#93c5fd">{url}</a></div>'
                            f'{r.text}</body></html>'
                        )
                    rendered_html_path.write_text(archive_html, encoding="utf-8")

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

"""
Archiver — برای X.com از nitter استفاده می‌کنه که محتوای واقعی داره.
برای بقیه سایت‌ها httpx با stealth headers.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

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

# nitter instances (آینه‌های توییتر که بدون JS کار می‌کنن)
NITTER_INSTANCES = [
    "https://nitter.net",
    "https://nitter.privacydev.net",
    "https://nitter.poast.org",
    "https://lightbrd.com",
]


def _safe_slug(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace(":", "_")
    path = parsed.path.strip("/").replace("/", "_") or "root"
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{host}_{path}_{ts}"


def _twitter_to_nitter_path(url: str) -> str | None:
    """تبدیل لینک X.com/Twitter به path برای nitter"""
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if any(x in host for x in ["x.com", "twitter.com"]):
        return parsed.path  # مثلاً /username/status/123
    return None


def _extract_tweet_content(html: str, original_url: str) -> str:
    """یه HTML تمیز از محتوای nitter می‌سازه"""
    # پیدا کردن متن توییت
    tweet_match = re.search(r'<div class="tweet-content[^"]*"[^>]*>(.*?)</div>', html, re.DOTALL)
    tweet_text = ""
    if tweet_match:
        tweet_text = re.sub(r'<[^>]+>', '', tweet_match.group(1)).strip()

    # پیدا کردن تصاویر
    images = re.findall(r'<img[^>]+src="([^"]*pic[^"]*)"', html)
    img_tags = ""
    for img in images[:4]:
        if img.startswith("/"):
            img = f"https://nitter.net{img}"
        img_tags += f'<img src="{img}" style="max-width:100%;margin:8px 0;border-radius:8px;" /><br/>'

    # پیدا کردن اطلاعات کاربر
    user_match = re.search(r'<a class="fullname"[^>]*>([^<]+)</a>', html)
    username_match = re.search(r'<a class="username"[^>]*>([^<]+)</a>', html)
    date_match = re.search(r'<span class="tweet-date"[^>]*><a[^>]*>([^<]+)</a>', html)

    user = user_match.group(1) if user_match else ""
    username = username_match.group(1) if username_match else ""
    date = date_match.group(1) if date_match else ""

    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Archive — {original_url}</title>
<style>
body{{font-family:Tahoma,sans-serif;background:#f0f4f8;margin:0;padding:16px;}}
.banner{{background:#1d4ed8;color:#fff;padding:10px 16px;border-radius:8px;margin-bottom:16px;font-size:13px;}}
.banner a{{color:#93c5fd;}}
.card{{background:#fff;border-radius:16px;padding:20px;max-width:600px;margin:0 auto;
       box-shadow:0 4px 20px rgba(0,0,0,.08);}}
.user{{display:flex;align-items:center;gap:10px;margin-bottom:12px;}}
.fullname{{font-weight:bold;font-size:16px;}}
.username{{color:#666;font-size:14px;}}
.date{{color:#888;font-size:12px;margin-bottom:12px;}}
.content{{font-size:16px;line-height:1.6;white-space:pre-wrap;word-break:break-word;}}
.images{{margin-top:12px;}}
.source{{margin-top:16px;padding-top:12px;border-top:1px solid #eee;font-size:12px;color:#888;}}
</style>
</head>
<body>
<div class="banner">
  📦 Archive Hub — آرشیو از <a href="{original_url}">{original_url}</a>
</div>
<div class="card">
  <div class="user">
    <div>
      <div class="fullname">{user}</div>
      <div class="username">{username}</div>
    </div>
  </div>
  <div class="date">{date}</div>
  <div class="content">{tweet_text}</div>
  <div class="images">{img_tags}</div>
  <div class="source">منبع: <a href="{original_url}">{original_url}</a></div>
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
        screenshot_path.write_bytes(b"")  # خالی پیش‌فرض

        nitter_path = _twitter_to_nitter_path(url)

        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers=STEALTH_HEADERS,
        ) as client:

            if nitter_path:
                # ── X.com/Twitter: از nitter استفاده کن ─────────────────────
                html = None
                for instance in NITTER_INSTANCES:
                    nitter_url = f"{instance}{nitter_path}"
                    try:
                        logger.info("Trying nitter: %s", nitter_url)
                        r = await client.get(nitter_url, timeout=15)
                        if r.status_code == 200 and "tweet-content" in r.text:
                            html = r.text
                            logger.info("Got content from %s", instance)
                            break
                    except Exception as e:
                        logger.warning("Nitter %s failed: %s", instance, e)

                if html:
                    raw_html_path.write_text(html, encoding="utf-8")
                    archive_html = _extract_tweet_content(html, url)
                    rendered_html_path.write_text(archive_html, encoding="utf-8")
                else:
                    # fallback: مستقیم از X.com بگیر
                    logger.warning("All nitter instances failed, trying direct fetch")
                    try:
                        r = await client.get(url)
                        raw_html_path.write_text(r.text, encoding="utf-8")
                        rendered_html_path.write_text(
                            f"""<!DOCTYPE html><html><head><meta charset="UTF-8"/>
                            <title>Archive</title></head><body>
                            <div style="background:#1d4ed8;color:#fff;padding:10px;">
                            📦 Archive Hub — <a href="{url}" style="color:#93c5fd">{url}</a></div>
                            {r.text}</body></html>""",
                            encoding="utf-8",
                        )
                    except Exception as e:
                        raw_html_path.write_text(f"<!-- fetch failed: {e} -->", encoding="utf-8")
                        rendered_html_path.write_text(
                            f'<html><body><p>آرشیو ناموفق: {e}</p><p><a href="{url}">{url}</a></p></body></html>',
                            encoding="utf-8",
                        )
            else:
                # ── سایر سایت‌ها ─────────────────────────────────────────────
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    raw_html_path.write_text(r.text, encoding="utf-8")
                    rendered_html_path.write_text(
                        f'<html><head><meta charset="UTF-8"/></head><body>'
                        f'<div style="background:#1d4ed8;color:#fff;padding:10px;">'
                        f'📦 Archive Hub — <a href="{url}" style="color:#93c5fd">{url}</a></div>'
                        f'{r.text}</body></html>',
                        encoding="utf-8",
                    )
                except Exception as e:
                    logger.error("Fetch failed: %s", e)
                    raw_html_path.write_text(f"<!-- fetch failed: {e} -->", encoding="utf-8")
                    rendered_html_path.write_text(
                        f'<html><body><p>آرشیو ناموفق: {e}</p></body></html>',
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

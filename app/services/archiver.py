"""
Archiver — screenshot از screenshotmachine + محتوا از microlink
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


def _is_twitter(url: str) -> bool:
    return "x.com" in url.lower() or "twitter.com" in url.lower()


async def _get_screenshot(url: str, client: httpx.AsyncClient) -> bytes:
    """screenshot از screenshotmachine — قبلاً کار می‌کرد"""
    key = settings.screenshot_machine_key or "dd29ad"
    encoded = quote(url, safe="")
    sm_url = (
        "https://api.screenshotmachine.com/"
        "?key=" + key +
        "&url=" + encoded +
        "&dimension=1280x900&format=png&delay=4000"
    )
    try:
        r = await client.get(sm_url, timeout=45)
        ct = r.headers.get("content-type", "")
        if r.status_code == 200 and ct.startswith("image") and len(r.content) > 5000:
            logger.info("screenshot ok: %d bytes", len(r.content))
            return r.content
        logger.warning("screenshotmachine bad response: %s %d bytes", ct, len(r.content))
    except Exception as e:
        logger.warning("screenshotmachine failed: %s", e)

    # fallback: thum.io
    try:
        thumb = "https://image.thum.io/get/width/1280/crop/900/" + encoded
        r2 = await client.get(thumb, timeout=30)
        if r2.status_code == 200 and r2.headers.get("content-type","").startswith("image"):
            return r2.content
    except Exception as e:
        logger.warning("thum.io failed: %s", e)

    return b""


async def _get_microlink(url: str, client: httpx.AsyncClient) -> dict:
    try:
        r = await client.get(
            "https://api.microlink.io/",
            params={"url": url, "meta": "true", "video": "false"},
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json().get("data", {})
            return data
    except Exception as e:
        logger.warning("microlink failed: %s", e)
    return {}


def _make_archive_html(url: str, content: str, title: str, author: str, date: str) -> str:
    now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    return """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>""" + (title or url) + """</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:Tahoma,Arial,sans-serif;background:#f8fafc;direction:rtl;}
.banner{background:#1e40af;color:#fff;padding:10px 20px;font-size:13px;
  display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
.banner a{color:#93c5fd;text-decoration:none;}
.banner .ml{margin-right:auto;}
.card{max-width:700px;margin:32px auto;background:#fff;border-radius:16px;
  box-shadow:0 4px 24px rgba(0,0,0,.08);padding:32px;}
.meta{color:#64748b;font-size:13px;margin-bottom:16px;display:flex;gap:16px;flex-wrap:wrap;}
h2{font-size:1.3rem;margin-bottom:16px;color:#1e293b;}
.body{font-size:1rem;line-height:1.9;color:#334155;white-space:pre-wrap;word-break:break-word;}
</style>
</head>
<body>
<div class="banner">
  📦 <strong>Archive Hub</strong>
  <span>""" + now_str + """</span>
  <a href=\"""" + url + """\" target="_blank" class="ml">🔗 لینک اصلی</a>
</div>
<div class="card">
  <div class="meta">
    """ + ("<span>👤 " + author + "</span>" if author else "") + """
    """ + ("<span>📅 " + date[:10] + "</span>" if date else "") + """
  </div>
  """ + ("<h2>" + title + "</h2>" if title else "") + """
  <div class="body">""" + content + """</div>
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
        post_meta: dict = {}

        async with httpx.AsyncClient(
            timeout=20,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 Chrome/122.0.0.0"},
        ) as client:

            # ── Screenshot ────────────────────────────────────────────────
            ss = await _get_screenshot(url, client)
            screenshot_path.write_bytes(ss)

            # ── محتوا ─────────────────────────────────────────────────────
            post_meta = {}

            if _is_twitter(url):
                meta = await _get_microlink(url, client)
                title = meta.get("title", "")
                description = meta.get("description", "")
                author = meta.get("author", "")
                publisher = meta.get("publisher", "")
                date = meta.get("date", "")

                # username از title: "Name (@user) on X"
                username = ""
                um = re.search(r'\(@([^)]+)\)', title)
                if um:
                    username = um.group(1)

                post_meta = {
                    "title": title,
                    "author": author or publisher,
                    "username": username,
                    "date": date,
                }

                content_text = re.sub(r'<[^>]+>', '', description or title or "")
                raw_data = "URL: " + url + "\nTitle: " + title + "\nAuthor: " + author + "\nDate: " + date + "\nContent: " + description
                raw_html_path.write_text(raw_data, encoding="utf-8")

                display_author = author
                if publisher and publisher != author:
                    display_author = author + " (" + publisher + ")"

                archive_html = _make_archive_html(
                    url=url,
                    content=content_text,
                    title=title,
                    author=display_author,
                    date=date,
                )
                rendered_html_path.write_text(archive_html, encoding="utf-8")

            else:
                try:
                    r = await client.get(url)
                    r.raise_for_status()
                    raw_html_path.write_text(r.text, encoding="utf-8")
                    meta = await _get_microlink(url, client)
                    title = meta.get("title", "")
                    description = meta.get("description", "")
                    author = meta.get("author", "")
                    date = meta.get("date", "")
                    post_meta = {"title": title, "author": author, "date": date}
                    content_text = re.sub(r'<[^>]+>', '', description or title or "")
                    archive_html = _make_archive_html(
                        url=url, content=content_text,
                        title=title, author=author, date=date,
                    )
                    rendered_html_path.write_text(archive_html, encoding="utf-8")
                except Exception as e:
                    logger.error("fetch failed: %s", e)
                    rendered_html_path.write_text("<p>Error: " + str(e) + "</p>", encoding="utf-8")
                    raw_html_path.write_text("", encoding="utf-8")

        return ArchiveArtifact(
            url=url,
            created_at=datetime.now(UTC),
            folder=folder,
            raw_html_path=raw_html_path,
            rendered_html_path=rendered_html_path,
            screenshot_path=screenshot_path,
            post_meta=post_meta,
        )

"""
Archiver — fetches URL and produces:
  1. raw.html      — plain HTTP fetch
  2. archive.html  — SingleFile-style self-contained HTML (CSS/images inlined)
  3. screenshot.png
"""
from __future__ import annotations

import base64
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from app.config import settings
from app.models import ArchiveArtifact

logger = logging.getLogger(__name__)

# Stealth headers to avoid bot detection on X/Twitter
STEALTH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
}


def _safe_slug(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.replace(":", "_")
    path = parsed.path.strip("/").replace("/", "_") or "root"
    ts = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"{host}_{path}_{ts}"


async def _inline_resources(html: str, base_url: str, client: httpx.AsyncClient) -> str:
    """
    Inline external CSS and images as base64 data URIs — SingleFile-style.
    Returns self-contained HTML.
    """
    # Inline <link rel="stylesheet"> → <style>
    async def fetch_css(match):
        href = match.group(1)
        if href.startswith("data:"):
            return match.group(0)
        try:
            url = urljoin(base_url, href)
            r = await client.get(url, timeout=10)
            return f"<style>{r.text}</style>"
        except Exception:
            return match.group(0)

    # Inline <img src> → data URI
    async def fetch_img(match):
        src = match.group(1)
        if src.startswith("data:"):
            return match.group(0)
        try:
            url = urljoin(base_url, src)
            r = await client.get(url, timeout=10)
            ct = r.headers.get("content-type", "image/png").split(";")[0]
            b64 = base64.b64encode(r.content).decode()
            return f'<img src="data:{ct};base64,{b64}"'
        except Exception:
            return match.group(0)

    # Process CSS links
    css_pattern = re.compile(r'<link[^>]+rel=["\']stylesheet["\'][^>]*href=["\']([^"\']+)["\'][^>]*/?>',
                              re.IGNORECASE)
    for m in css_pattern.finditer(html):
        replacement = await fetch_css(m)
        html = html.replace(m.group(0), replacement, 1)

    # Process img src (limit to reasonable size)
    img_pattern = re.compile(r'<img\s[^>]*src=["\']([^"\']+)["\']', re.IGNORECASE)
    for m in list(img_pattern.finditer(html))[:60]:  # max 60 images
        replacement = await fetch_img(m)
        html = html.replace(m.group(0), replacement, 1)

    return html


class Archiver:
    async def archive(self, url: str) -> ArchiveArtifact:
        slug = _safe_slug(url)
        folder = Path(settings.base_storage_dir) / slug
        folder.mkdir(parents=True, exist_ok=True)

        raw_html_path = folder / "raw.html"
        rendered_html_path = folder / "archive.html"
        screenshot_path = folder / "screenshot.png"

        # ── 1. Raw fetch with stealth headers ────────────────────────────────
        async with httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            headers=STEALTH_HEADERS,
        ) as client:
            try:
                response = await client.get(url)
                response.raise_for_status()
                raw_html_path.write_text(response.text, encoding="utf-8")
            except Exception as exc:
                logger.warning("Raw fetch failed: %s", exc)
                raw_html_path.write_text(f"<!-- fetch failed: {exc} -->", encoding="utf-8")

        # ── 2. Playwright render + screenshot + inline resources ──────────────
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(
                    args=[
                        "--no-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--disable-extensions",
                        "--disable-plugins-discovery",
                    ]
                )
                context = await browser.new_context(
                    viewport={"width": 1440, "height": 900},
                    user_agent=STEALTH_HEADERS["User-Agent"],
                    locale="en-US",
                    java_script_enabled=True,
                    # Block cookie consent / tracking scripts that break X
                    extra_http_headers={
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )

                # Inject stealth JS to hide webdriver flags
                await context.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', { get: () => false });
                    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3] });
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
                    window.chrome = { runtime: {} };
                """)

                page = await context.new_page()

                # Route: block tracking/analytics that cause X.com privacy errors
                await page.route(
                    re.compile(r"(google-analytics|doubleclick|facebook\.net|clarity\.ms|bat\.bing)"),
                    lambda route, _: route.abort(),
                )

                try:
                    await page.goto(url, wait_until="networkidle", timeout=settings.playwright_timeout_ms)
                except PlaywrightTimeoutError:
                    # networkidle times out on heavy SPAs — try domcontentloaded
                    await page.goto(url, wait_until="domcontentloaded", timeout=settings.playwright_timeout_ms)

                # Wait a bit more for SPA rendering (X.com needs this)
                await page.wait_for_timeout(3000)

                # Screenshot FIRST (before we mess with HTML)
                await page.screenshot(path=str(screenshot_path), full_page=True)

                rendered = await page.content()
                await browser.close()

            # ── 3. Inline CSS + images into single HTML file ──────────────────
            async with httpx.AsyncClient(
                timeout=15,
                follow_redirects=True,
                headers=STEALTH_HEADERS,
            ) as client:
                self_contained = await _inline_resources(rendered, url, client)

            # Add archive banner at top
            banner = (
                f'<div style="background:#1d4ed8;color:#fff;padding:8px 16px;font-family:sans-serif;font-size:13px;">'
                f'📦 Archive Hub — آرشیو شده از <a href="{url}" style="color:#93c5fd">{url}</a>'
                f'</div>'
            )
            self_contained = self_contained.replace("<body", f"{banner}<body", 1)
            rendered_html_path.write_text(self_contained, encoding="utf-8")

        except (PlaywrightTimeoutError, PlaywrightError) as exc:
            logger.error("Playwright failed: %s", exc)
            # Fallback: use raw HTML as archive
            rendered_html_path.write_text(
                f"<!-- Playwright failed: {exc} -->\n" + raw_html_path.read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            screenshot_path.write_bytes(b"")

        return ArchiveArtifact(
            url=url,
            created_at=datetime.now(UTC),
            folder=folder,
            raw_html_path=raw_html_path,
            rendered_html_path=rendered_html_path,
            screenshot_path=screenshot_path,
        )

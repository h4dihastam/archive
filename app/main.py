from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.services.archiver import Archiver
from app.storage.local import LocalStorageProvider
from app.storage.telegram import TelegramStorageProvider
from app.storage.supabase import get_supabase, save_archive
from app.utils import is_valid_url

logger = logging.getLogger(__name__)
app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
async def startup():
    if settings.telegram_bot_token and settings.webhook_url:
        import httpx
        endpoint = f"{settings.webhook_url.rstrip('/')}/bot/webhook"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                res = await client.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
                    json={"url": endpoint},
                )
                logger.info("Webhook set: %s → %s", endpoint, res.json().get("ok"))
        except Exception as e:
            logger.warning("Webhook setup failed: %s", e)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "result": None, "error": None})


@app.post("/archive", response_class=HTMLResponse)
async def do_archive(
    request: Request,
    url: str = Form(...),
    save_local: bool = Form(False),
    save_telegram: bool = Form(False),
):
    if not is_valid_url(url):
        return templates.TemplateResponse(
            "index.html", {"request": request, "error": "URL نامعتبر است.", "result": None}, status_code=400
        )

    archiver = Archiver()
    artifact = await archiver.archive(url)

    # Save to Supabase (if configured) — get archive_id
    archive_id = await save_archive(artifact)
    artifact.archive_id = archive_id
    artifact.public_url = f"{settings.archive_base}/view/{archive_id}"

    uploads: dict[str, dict[str, str]] = {}

    if save_local:
        p = LocalStorageProvider()
        files = {"archive.html": artifact.rendered_html_path, "screenshot.png": artifact.screenshot_path}
        uploads["local"] = {}
        for key, path in files.items():
            try:
                uri = await p.upload_file(path, key)
                uploads["local"][key] = uri
            except Exception as exc:
                uploads["local"][key] = f"ERROR: {exc}"

    if save_telegram:
        p = TelegramStorageProvider()
        files = {"archive.html": artifact.rendered_html_path, "screenshot.png": artifact.screenshot_path}
        uploads["telegram"] = {}
        for key, path in files.items():
            try:
                remote_name = f"{artifact.folder.name}_{path.name}"
                uri = await p.upload_file(path, remote_name)
                uploads["telegram"][key] = uri
            except Exception as exc:
                uploads["telegram"][key] = f"ERROR: {exc}"

    manifest = {
        "url": artifact.url,
        "archive_id": archive_id,
        "archive_link": artifact.public_url,
        "folder": str(artifact.folder),
        "uploads": uploads,
    }
    (artifact.folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "error": None, "result": manifest},
    )


@app.get("/view/{archive_id}", response_class=HTMLResponse)
async def view_archive(archive_id: str):
    """
    نمایش آرشیو — مثل archive.is:
    screenshot بزرگ + متن + اطلاعات
    """
    import httpx as _httpx

    row = None
    html_content = ""

    # ── ۱. اطلاعات از Supabase ───────────────────────────────────────────
    sb = get_supabase()
    if sb:
        try:
            rows = await sb.select("archives", {"id": archive_id})
            if rows:
                row = rows[0]
        except Exception as exc:
            logger.warning("Supabase select failed: %s", exc)

    # ── ۲. محتوای HTML از Supabase Storage ──────────────────────────────
    if row and row.get("html_url"):
        try:
            async with _httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                res = await client.get(row["html_url"])
                if res.status_code == 200:
                    html_content = res.text
        except Exception as exc:
            logger.warning("html fetch failed: %s", exc)

    # ── ۳. Local fallback ────────────────────────────────────────────────
    if not html_content:
        data_dir = Path(settings.base_storage_dir)
        if data_dir.exists():
            for folder in data_dir.iterdir():
                manifest_path = folder / "manifest.json"
                if manifest_path.exists():
                    try:
                        m = json.loads(manifest_path.read_text())
                        if m.get("archive_id") == archive_id:
                            html_path = folder / "archive.html"
                            if html_path.exists():
                                html_content = html_path.read_text(encoding="utf-8")
                                if not row:
                                    row = {"url": m.get("url",""), "created_at": ""}
                    except Exception:
                        pass

    if not html_content and not row:
        return HTMLResponse(
            f"""<html><head><meta charset="UTF-8"/></head>
            <body style="font-family:sans-serif;padding:40px;text-align:center;">
            <h2>⚠️ آرشیو یافت نشد</h2>
            <p><a href="/">برگشت</a></p></body></html>""",
            status_code=404,
        )

    # ── ۴. صفحه نمایش archive.is-style ──────────────────────────────────
    orig_url = row.get("url", "") if row else ""
    screenshot_url = row.get("screenshot_url", "") if row else ""
    created_at = (row.get("created_at", "") or "")[:19].replace("T", " ") if row else ""

    # استخراج متن از html_content
    import re as _re
    text_content = ""
    if html_content:
        # پیدا کردن div.content یا body
        m = _re.search(r'<div class="content">(.*?)</div>', html_content, _re.DOTALL)
        if m:
            text_content = _re.sub(r'<[^>]+>', '', m.group(1)).strip()

    screenshot_section = ""
    if screenshot_url:
        screenshot_section = f'''
        <div class="ss-wrap">
          <img src="{screenshot_url}" alt="screenshot" class="ss-img"/>
        </div>'''

    page = f"""<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>آرشیو — {orig_url[:60]}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:Tahoma,Arial,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh;}}
.topbar{{background:#1e3a8a;padding:10px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}}
.topbar .logo{{font-weight:bold;font-size:16px;color:#fff;}}
.topbar .orig-url{{font-size:12px;color:#93c5fd;word-break:break-all;}}
.topbar .date{{font-size:11px;color:#bfdbfe;margin-right:auto;white-space:nowrap;}}
.container{{max-width:900px;margin:24px auto;padding:0 16px;}}
.meta-card{{background:#1e293b;border-radius:12px;padding:16px 20px;margin-bottom:20px;
            display:flex;gap:16px;flex-wrap:wrap;align-items:center;}}
.meta-card a{{color:#60a5fa;text-decoration:none;font-size:13px;word-break:break-all;}}
.meta-card .badge{{background:#1d4ed8;color:#fff;border-radius:6px;padding:3px 10px;font-size:11px;}}
.ss-wrap{{background:#1e293b;border-radius:12px;overflow:hidden;margin-bottom:20px;
          border:1px solid #334155;}}
.ss-img{{width:100%;display:block;}}
.content-card{{background:#1e293b;border-radius:12px;padding:20px;border:1px solid #334155;}}
.content-card .label{{font-size:11px;color:#64748b;margin-bottom:8px;text-transform:uppercase;}}
.content-card .text{{font-size:16px;line-height:1.8;white-space:pre-wrap;word-break:break-word;color:#e2e8f0;}}
.no-ss{{background:#0f172a;border:2px dashed #334155;border-radius:12px;padding:40px;
        text-align:center;color:#475569;margin-bottom:20px;font-size:14px;}}
</style>
</head>
<body>
<div class="topbar">
  <span class="logo">📦 Archive Hub</span>
  <a class="orig-url" href="{orig_url}" target="_blank">{orig_url}</a>
  <span class="date">🕐 {created_at}</span>
</div>
<div class="container">
  <div class="meta-card">
    <span class="badge">آرشیو شده</span>
    <a href="{orig_url}" target="_blank">مشاهده لینک اصلی ↗</a>
  </div>

  {screenshot_section if screenshot_section else '<div class="no-ss">📸 اسکرین‌شات در دسترس نیست</div>'}

  {f'<div class="content-card"><div class="label">متن پست</div><div class="text">{text_content}</div></div>' if text_content else ""}
</div>
</body>
</html>"""

    return HTMLResponse(page)


@app.get("/screenshot/{archive_id}")
async def view_screenshot(archive_id: str):
    """Return screenshot PNG."""
    data_dir = Path(settings.base_storage_dir)
    for folder in data_dir.iterdir():
        manifest_path = folder / "manifest.json"
        if manifest_path.exists():
            try:
                m = json.loads(manifest_path.read_text())
                if m.get("archive_id") == archive_id:
                    ss = folder / "screenshot.png"
                    if ss.exists() and ss.stat().st_size > 0:
                        return FileResponse(str(ss), media_type="image/png")
            except Exception:
                pass

    return Response(status_code=404)


@app.post("/bot/webhook")
async def bot_webhook(request: Request):
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
    import asyncio
    from app.bot import handle_update
    asyncio.create_task(handle_update(update))
    return JSONResponse({"ok": True})


@app.get("/bot/set_webhook")
async def set_webhook(request: Request):
    if not settings.telegram_bot_token:
        return JSONResponse({"error": "TELEGRAM_BOT_TOKEN not set"})
    if not settings.webhook_url:
        return JSONResponse({"error": "WEBHOOK_URL not set"})
    import httpx
    endpoint = f"{settings.webhook_url.rstrip('/')}/bot/webhook"
    async with httpx.AsyncClient(timeout=10) as client:
        res = await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
            json={"url": endpoint},
        )
        return JSONResponse(res.json())

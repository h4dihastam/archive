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
            "index.html", 
            {"request": request, "error": "URL نامعتبر است.", "result": None}, 
            status_code=400
        )

    archiver = Archiver()
    
    # === فیکس مهم: مدیریت خطا قوی برای جلوگیری از ERR_CONNECTION_CLOSED ===
    try:
        artifact = await archiver.archive(url)
    except Exception as e:
        logger.exception(f"Archive failed for URL: {url}")
        return templates.TemplateResponse(
            "index.html",
            {
                "request": request, 
                "error": f"خطا در آرشیو صفحه: {str(e)[:250]}", 
                "result": None
            },
            status_code=500
        )

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
        "title": getattr(artifact, 'post_meta', {}).get("title", "") if hasattr(artifact, 'post_meta') else "",
        "screenshot_url": f"/screenshot/{archive_id}" if artifact.screenshot_path.exists() and artifact.screenshot_path.stat().st_size > 0 else None,
    }

    (artifact.folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), 
        encoding="utf-8"
    )

    return templates.TemplateResponse(
        "index.html",
        {"request": request, "error": None, "result": manifest},
    )


@app.get("/view/{archive_id}", response_class=HTMLResponse)
async def view_archive(archive_id: str):
    """نمایش آرشیو کامل"""
    import httpx as _httpx

    row = None
    html_content = ""

    sb = get_supabase()
    if sb:
        try:
            rows = await sb.select("archives", {"id": archive_id})
            if rows:
                row = rows[0]
        except Exception as exc:
            logger.warning("Supabase select failed: %s", exc)

    if row and row.get("html_url"):
        try:
            async with _httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                res = await client.get(row["html_url"])
                if res.status_code == 200:
                    html_content = res.text
        except Exception as exc:
            logger.warning("html fetch failed: %s", exc)

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

    if not html_content:
        return HTMLResponse(
            """<html><head><meta charset="UTF-8"/></head>
            <body style="font-family:sans-serif;padding:60px;text-align:center;background:#0f172a;color:white;">
            <h2>⚠️ آرشیو یافت نشد</h2>
            <p><a href="/" style="color:#60a5fa;">برگشت به خانه</a></p></body></html>""",
            status_code=404,
        )

    return HTMLResponse(html_content)


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
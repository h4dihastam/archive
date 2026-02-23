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
    sb = get_supabase()
    row = None
    if sb:
        try:
            rows = await sb.select("archives", {"id": archive_id})
            if rows:
                row = rows[0]
        except Exception as exc:
            logger.warning("Supabase select failed: %s", exc)

    if not row:
        return HTMLResponse("<h2>آرشیو یافت نشد</h2>", status_code=404)

    orig_url = row.get("url", "")
    screenshot_url = row.get("screenshot_url", "")
    html_url = row.get("html_url", "")
    created_at = (row.get("created_at", "") or "")[:19].replace("T", " ")
    author = row.get("post_author", "") or row.get("post_username", "")
    if row.get("post_username") and row.get("post_author"):
        author = row["post_author"] + " (@" + row["post_username"] + ")"
    elif row.get("post_username"):
        author = "@" + row["post_username"]

    base = settings.archive_base or ""
    web_link = (base + "/web/" + archive_id) if base else ""

    page = """<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>آرشیو</title>
<link href="https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;600;700&display=swap" rel="stylesheet"/>
<style>
*{box-sizing:border-box;margin:0;padding:0;}
body{font-family:'Vazirmatn',sans-serif;background:#060910;color:#e2e8f0;min-height:100vh;}
.bar{background:#1e3a8a;padding:12px 20px;display:flex;align-items:center;gap:12px;flex-wrap:wrap;}
.bar .logo{font-weight:700;color:#fff;font-size:15px;}
.bar a{color:#93c5fd;font-size:12px;word-break:break-all;text-decoration:none;}
.bar .date{font-size:11px;color:#bfdbfe;margin-right:auto;}
.wrap{max-width:860px;margin:24px auto;padding:0 16px;display:flex;flex-direction:column;gap:16px;}
.card{background:rgba(255,255,255,.05);border:1px solid rgba(99,102,241,.2);border-radius:16px;padding:20px;}
.meta{display:flex;gap:12px;flex-wrap:wrap;align-items:center;}
.badge{background:#1d4ed8;color:#fff;border-radius:6px;padding:4px 12px;font-size:12px;font-weight:600;}
.btn{padding:10px 20px;border-radius:10px;font-size:13px;font-weight:600;text-decoration:none;display:inline-block;transition:.2s;}
.btn-blue{background:rgba(99,102,241,.15);border:1px solid rgba(99,102,241,.4);color:#a5b4fc;}
.btn-blue:hover{background:rgba(99,102,241,.3);}
.btn-cyan{background:rgba(6,182,212,.15);border:1px solid rgba(6,182,212,.4);color:#67e8f9;}
.btn-cyan:hover{background:rgba(6,182,212,.3);}
.ss-img{width:100%;border-radius:12px;display:block;}
.no-ss{padding:40px;text-align:center;color:#475569;font-size:14px;}
</style>
</head>
<body>
<div class="bar">
  <span class="logo">📦 Archive Hub</span>
  <a href="""" + orig_url + """" target="_blank">""" + orig_url[:80] + """</a>
  <span class="date">🕐 """ + created_at + """</span>
</div>
<div class="wrap">
  <div class="card">
    <div class="meta">
      <span class="badge">✅ آرشیو شده</span>
      """ + ('<span style="color:#a5b4fc;font-size:14px;">👤 ' + author + '</span>' if author else '') + """
      <a href="""" + orig_url + """" target="_blank" class="btn btn-blue">🔗 لینک اصلی ↗</a>
      """ + ('<a href="' + web_link + '" target="_blank" class="btn btn-cyan">🌐 مشاهده آرشیو وب</a>' if web_link else '') + """
      """ + ('<a href="' + html_url + '" target="_blank" class="btn btn-blue">⬇️ دانلود HTML</a>' if html_url else '') + """
    </div>
  </div>

  <div class="card">
    """ + ('<img src="' + screenshot_url + '" class="ss-img" alt="screenshot"/>' if screenshot_url else '<div class="no-ss">📸 اسکرین‌شات موجود نیست</div>') + """
  </div>
</div>
</body>
</html>"""

    return HTMLResponse(page)


@app.get("/web/{archive_id}", response_class=HTMLResponse)
async def view_web_archive(archive_id: str):
    """نمایش HTML کامل آرشیو شده"""
    sb = get_supabase()
    if sb:
        try:
            rows = await sb.select("archives", {"id": archive_id})
            if rows and rows[0].get("html_url"):
                async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                    r = await c.get(rows[0]["html_url"])
                    if r.status_code == 200:
                        return HTMLResponse(r.text)
        except Exception as exc:
            logger.warning("web archive fetch failed: %s", exc)
    return HTMLResponse("<h2>آرشیو یافت نشد</h2>", status_code=404)


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


# ── Admin Panel ───────────────────────────────────────────────────────────────

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel(request: Request):
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "admin_password": settings.bot_password or "admin",
    })


def _check_admin(request: Request) -> bool:
    key = request.headers.get("X-Admin-Key", "")
    return key == (settings.bot_password or "admin")


@app.get("/admin/api/stats")
async def admin_stats(request: Request):
    if not _check_admin(request):
        from fastapi import HTTPException
        raise HTTPException(403)
    sb = get_supabase()
    if not sb:
        return {"total_archives": 0, "total_users": 0, "file_count": 0, "total_bytes": 0}
    async with httpx.AsyncClient(timeout=15) as c:
        headers = {"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}
        r1 = await c.get(f"{sb.base}/rest/v1/archives",
                         headers={**headers, "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"})
        total_archives = int((r1.headers.get("content-range","0/?").split("/")[-1]) or 0)
        r2 = await c.get(f"{sb.base}/rest/v1/bot_users",
                         headers={**headers, "Prefer": "count=exact", "Range-Unit": "items", "Range": "0-0"})
        total_users = int((r2.headers.get("content-range","0/?").split("/")[-1]) or 0)
        r3 = await c.post(f"{sb.base}/rest/v1/rpc/get_storage_stats",
                          headers={**headers, "Content-Type": "application/json"}, json={})
        file_count, total_bytes = 0, 0
        if r3.is_success and r3.json():
            row = r3.json()[0] if isinstance(r3.json(), list) else r3.json()
            file_count = int(row.get("file_count", 0) or 0)
            total_bytes = int(row.get("total_bytes", 0) or 0)
    return {"total_archives": total_archives, "total_users": total_users,
            "file_count": file_count, "total_bytes": total_bytes}


@app.get("/admin/api/archives")
async def admin_archives(request: Request):
    if not _check_admin(request):
        from fastapi import HTTPException
        raise HTTPException(403)
    sb = get_supabase()
    if not sb:
        return []
    async with httpx.AsyncClient(timeout=15) as c:
        headers = {"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}
        r = await c.get(f"{sb.base}/rest/v1/archives", headers=headers,
                        params={"order": "created_at.desc", "limit": "100"})
        return r.json() if r.is_success else []


@app.get("/admin/api/users")
async def admin_users(request: Request):
    if not _check_admin(request):
        from fastapi import HTTPException
        raise HTTPException(403)
    sb = get_supabase()
    if not sb:
        return []
    async with httpx.AsyncClient(timeout=15) as c:
        headers = {"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}
        r = await c.get(f"{sb.base}/rest/v1/bot_users", headers=headers,
                        params={"order": "created_at.desc"})
        return r.json() if r.is_success else []


@app.delete("/admin/api/delete/{archive_id}")
async def admin_delete(archive_id: str, request: Request):
    if not _check_admin(request):
        from fastapi import HTTPException
        raise HTTPException(403)
    from app.storage.supabase import get_supabase as _gsb
    sb = _gsb()
    if not sb:
        return {"ok": False}
    async with httpx.AsyncClient(timeout=15) as c:
        headers = {"apikey": sb.key, "Authorization": f"Bearer {sb.key}"}
        await c.delete(f"{sb.base}/rest/v1/archives", headers=headers,
                       params={"id": f"eq.{archive_id}"})
        for fname in ["archive.html", "raw.html", "screenshot.png"]:
            await c.delete(f"{sb.base}/storage/v1/object/{sb.bucket}/{archive_id}/{fname}",
                           headers=headers)
    return {"ok": True}

from __future__ import annotations

import httpx
import json
import logging
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.services.archiver import Archiver
from app.storage.supabase import get_supabase, save_archive
from app.utils import is_valid_url

logger = logging.getLogger(__name__)
app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.on_event("startup")
async def startup():
    if settings.telegram_bot_token and settings.webhook_url:
        endpoint = f"{settings.webhook_url.rstrip('/')}/bot/webhook"
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.post(
                    f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
                    json={"url": endpoint},
                )
                logger.info("Webhook set: %s → %s", endpoint, r.json().get("ok"))
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
            status_code=400,
        )

    try:
        artifact = await Archiver().archive(url)
    except Exception as e:
        logger.exception("Archive failed: %s", url)
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": f"خطا در آرشیو: {str(e)[:200]}", "result": None},
            status_code=500,
        )

    archive_id = await save_archive(artifact)
    artifact.archive_id = archive_id
    artifact.public_url = f"{settings.archive_base}/view/{archive_id}"

    # ارسال به تلگرام از سایت
    if save_telegram:
        try:
            TGAPI = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
            target = settings.telegram_chat_id
            if target:
                async with httpx.AsyncClient(timeout=60) as tc:
                    cap = f"📦 archive.html\n🔗 {url}\n🌐 {artifact.public_url}"
                    with artifact.rendered_html_path.open("rb") as f:
                        await tc.post(f"{TGAPI}/sendDocument",
                                      data={"chat_id": target, "caption": cap},
                                      files={"document": ("archive.html", f)})
                    if artifact.screenshot_path.exists() and artifact.screenshot_path.stat().st_size > 2000:
                        with artifact.screenshot_path.open("rb") as f:
                            await tc.post(f"{TGAPI}/sendPhoto",
                                          data={"chat_id": target, "caption": f"📸 {url}"},
                                          files={"photo": ("screenshot.png", f)})
        except Exception as e:
            logger.warning("Telegram send failed: %s", e)

    # screenshot_url برای نمایش در سایت — از Supabase
    screenshot_url = ""
    sb = get_supabase()
    if sb and archive_id:
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                r = await c.get(
                    sb.base + "/rest/v1/archives",
                    headers={"apikey": sb.key, "Authorization": "Bearer " + sb.key},
                    params={"id": "eq." + archive_id, "select": "screenshot_url"},
                )
                rows = r.json() if r.is_success else []
                if rows:
                    screenshot_url = rows[0].get("screenshot_url", "")
        except Exception:
            pass

    # fallback: local screenshot endpoint
    if not screenshot_url and artifact.screenshot_path.exists() and artifact.screenshot_path.stat().st_size > 2000:
        screenshot_url = f"/screenshot/{archive_id}"

    manifest = {
        "url": url,
        "archive_id": archive_id,
        "archive_link": artifact.public_url,
        "screenshot_url": screenshot_url,
    }
    (artifact.folder / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    return templates.TemplateResponse("index.html", {"request": request, "error": None, "result": manifest})


# ────────────────────────────────────────────────────────────────────────────
# /view/{id} — صفحه متادیتا + screenshot + دکمه‌ها
# ────────────────────────────────────────────────────────────────────────────
@app.get("/view/{archive_id}", response_class=HTMLResponse)
async def view_archive(archive_id: str):
    sb = get_supabase()
    row = None

    if sb:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(
                    sb.base + "/rest/v1/archives",
                    headers={
                        "apikey": sb.key,
                        "Authorization": "Bearer " + sb.key,
                        "Accept": "application/json",
                    },
                    params={"id": "eq." + archive_id, "limit": "1"},
                )
                logger.info("view_archive status=%s body=%s", r.status_code, r.text[:300])
                if r.is_success and r.json():
                    row = r.json()[0]
        except Exception as e:
            logger.warning("view_archive DB error: %s", e)

    # fallback: جستجو در فایل‌های local
    if not row:
        data_dir = Path(settings.base_storage_dir)
        if data_dir.exists():
            for folder in data_dir.iterdir():
                mf = folder / "manifest.json"
                if mf.exists():
                    try:
                        m = json.loads(mf.read_text())
                        if m.get("archive_id") == archive_id:
                            row = {"url": m.get("url", ""), "created_at": "",
                                   "screenshot_url": "", "html_url": ""}
                    except Exception:
                        pass

    if not row:
        return HTMLResponse(
            "<html dir='rtl'><head><meta charset='UTF-8'/><style>"
            "body{font-family:sans-serif;background:#060910;color:#e2e8f0;"
            "display:flex;align-items:center;justify-content:center;min-height:100vh;}</style></head>"
            "<body><div style='text-align:center'>"
            "<h2>⚠️ آرشیو یافت نشد</h2>"
            f"<p style='color:#64748b;margin-top:8px'>شناسه: {archive_id}</p>"
            "<a href='/' style='color:#6366f1;margin-top:16px;display:block'>برگشت به صفحه اصلی</a>"
            "</div></body></html>",
            status_code=404,
        )

    orig_url = row.get("url", "")
    screenshot_url = row.get("screenshot_url", "")
    html_url = row.get("html_url", "")
    created_at = (row.get("created_at", "") or "")[:19].replace("T", " ")
    author = ""
    if row.get("post_username") and row.get("post_author"):
        author = row["post_author"] + " (@" + row["post_username"] + ")"
    elif row.get("post_username"):
        author = "@" + row["post_username"]
    elif row.get("post_author"):
        author = row["post_author"]

    base = settings.archive_base or ""
    web_link = f"{base}/web/{archive_id}" if base else ""

    ss_html = ""
    if screenshot_url:
        ss_html = (
            "<div class='ss-wrap'>"
            "<div class='ss-bar'>"
            "<div class='dot' style='background:#ef4444'></div>"
            "<div class='dot' style='background:#eab308'></div>"
            "<div class='dot' style='background:#22c55e'></div>"
            f"<span style='color:#64748b;font-size:11px;margin-right:8px;'>{orig_url[:60]}</span>"
            "</div>"
            f"<img src='{screenshot_url}' class='ss-img' alt='screenshot'/>"
            "</div>"
        )
    else:
        ss_html = "<div class='no-ss'>📸 اسکرین‌شات موجود نیست</div>"

    author_html = f"<span class='author'>👤 {author}</span>" if author else ""
    web_btn = f"<a href='{web_link}' target='_blank' class='btn btn-cyan'>👁 مشاهده آرشیو وب</a>" if web_link else ""
    dl_btn = f"<a href='{html_url}' download class='btn btn-green'>⬇️ دانلود HTML</a>" if html_url else ""
    orig_short = orig_url[:65] + ("..." if len(orig_url) > 65 else "")

    page = f"""<!DOCTYPE html>
<html lang='fa' dir='rtl'>
<head>
<meta charset='UTF-8'/>
<meta name='viewport' content='width=device-width,initial-scale=1'/>
<title>آرشیو — Archive Hub</title>
<link href='https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;600;700&display=swap' rel='stylesheet'/>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Vazirmatn',sans-serif;background:#060910;color:#e2e8f0;min-height:100vh;}}
.bar{{background:#1e3a8a;padding:12px 20px;display:flex;align-items:center;gap:10px;flex-wrap:wrap;position:sticky;top:0;z-index:100;}}
.bar .logo{{font-weight:700;color:#fff;font-size:15px;white-space:nowrap;}}
.bar a{{color:#93c5fd;font-size:12px;word-break:break-all;text-decoration:none;}}
.bar .date{{font-size:11px;color:#bfdbfe;margin-right:auto;white-space:nowrap;}}
.wrap{{max-width:960px;margin:24px auto;padding:0 16px;display:flex;flex-direction:column;gap:16px;}}
.card{{background:rgba(255,255,255,.05);border:1px solid rgba(99,102,241,.2);border-radius:16px;padding:20px;}}
.meta{{display:flex;gap:10px;flex-wrap:wrap;align-items:center;}}
.badge{{background:#1d4ed8;color:#fff;border-radius:6px;padding:4px 12px;font-size:12px;font-weight:600;}}
.author{{color:#a5b4fc;font-size:14px;}}
.btn{{padding:10px 20px;border-radius:10px;font-size:13px;font-weight:600;text-decoration:none;display:inline-flex;align-items:center;gap:6px;transition:.2s;}}
.btn-blue{{background:rgba(99,102,241,.15);border:1px solid rgba(99,102,241,.4);color:#a5b4fc;}}
.btn-blue:hover{{background:rgba(99,102,241,.3);}}
.btn-cyan{{background:rgba(6,182,212,.15);border:1px solid rgba(6,182,212,.4);color:#67e8f9;}}
.btn-cyan:hover{{background:rgba(6,182,212,.3);}}
.btn-green{{background:rgba(34,197,94,.12);border:1px solid rgba(34,197,94,.3);color:#86efac;}}
.btn-green:hover{{background:rgba(34,197,94,.25);}}
.ss-wrap{{border-radius:12px;overflow:hidden;border:1px solid rgba(255,255,255,.08);background:#0f172a;}}
.ss-bar{{background:#1e293b;padding:8px 12px;display:flex;gap:6px;align-items:center;}}
.dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0;}}
.ss-img{{width:100%;display:block;max-height:700px;object-fit:cover;object-position:top;}}
.no-ss{{padding:40px;text-align:center;color:#475569;font-size:14px;}}
</style>
</head>
<body>
<div class='bar'>
  <span class='logo'>📦 Archive Hub</span>
  <a href='{orig_url}' target='_blank'>{orig_short}</a>
  <span class='date'>🕐 {created_at}</span>
</div>
<div class='wrap'>
  <div class='card'>
    <div class='meta'>
      <span class='badge'>✅ آرشیو شده</span>
      {author_html}
      <a href='{orig_url}' target='_blank' class='btn btn-blue'>🔗 لینک اصلی ↗</a>
      {web_btn}
      {dl_btn}
    </div>
  </div>
  <div class='card'>{ss_html}</div>
</div>
</body>
</html>"""

    return HTMLResponse(page)


# ────────────────────────────────────────────────────────────────────────────
# /web/{id} — HTML کامل آرشیو شده
# ────────────────────────────────────────────────────────────────────────────
@app.get("/web/{archive_id}", response_class=HTMLResponse)
async def view_web_archive(archive_id: str):
    sb = get_supabase()
    html_url = ""

    if sb:
        try:
            async with httpx.AsyncClient(timeout=15) as c:
                r = await c.get(
                    sb.base + "/rest/v1/archives",
                    headers={
                        "apikey": sb.key,
                        "Authorization": "Bearer " + sb.key,
                        "Accept": "application/json",
                    },
                    params={"id": "eq." + archive_id, "select": "html_url", "limit": "1"},
                )
                logger.info("web_archive status=%s body=%s", r.status_code, r.text[:200])
                if r.is_success and r.json():
                    html_url = r.json()[0].get("html_url", "")
        except Exception as e:
            logger.warning("web_archive DB error: %s", e)

    # دانلود HTML از Supabase Storage
    if html_url:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                r2 = await c.get(html_url)
                if r2.status_code == 200:
                    return HTMLResponse(r2.text)
        except Exception as e:
            logger.warning("html fetch error: %s", e)

    # fallback: فایل local
    data_dir = Path(settings.base_storage_dir)
    if data_dir.exists():
        for folder in data_dir.iterdir():
            mf = folder / "manifest.json"
            if mf.exists():
                try:
                    m = json.loads(mf.read_text())
                    if m.get("archive_id") == archive_id:
                        html_path = folder / "archive.html"
                        if html_path.exists():
                            return HTMLResponse(html_path.read_text(encoding="utf-8"))
                except Exception:
                    pass

    return HTMLResponse(
        "<html dir='rtl'><head><meta charset='UTF-8'/></head>"
        "<body style='font-family:sans-serif;padding:40px;background:#060910;color:#e2e8f0;text-align:center;'>"
        "<h2>⚠️ فایل آرشیو یافت نشد</h2>"
        f"<p style='color:#64748b;margin-top:8px'>{archive_id}</p>"
        f"<a href='/view/{archive_id}' style='color:#6366f1;margin-top:16px;display:block'>برگشت به صفحه آرشیو</a>"
        "</body></html>",
        status_code=404,
    )


# ────────────────────────────────────────────────────────────────────────────
# /screenshot/{id} — local fallback
# ────────────────────────────────────────────────────────────────────────────
@app.get("/screenshot/{archive_id}")
async def view_screenshot(archive_id: str):
    data_dir = Path(settings.base_storage_dir)
    if data_dir.exists():
        for folder in data_dir.iterdir():
            mf = folder / "manifest.json"
            if mf.exists():
                try:
                    m = json.loads(mf.read_text())
                    if m.get("archive_id") == archive_id:
                        ss = folder / "screenshot.png"
                        if ss.exists() and ss.stat().st_size > 0:
                            return FileResponse(str(ss), media_type="image/png")
                except Exception:
                    pass
    return Response(status_code=404)


# ────────────────────────────────────────────────────────────────────────────
# Bot webhook
# ────────────────────────────────────────────────────────────────────────────
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
    endpoint = f"{settings.webhook_url.rstrip('/')}/bot/webhook"
    async with httpx.AsyncClient(timeout=10) as c:
        r = await c.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/setWebhook",
            json={"url": endpoint},
        )
        return JSONResponse(r.json())

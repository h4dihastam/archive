from __future__ import annotations

import asyncio
import httpx
import json
import logging
import uuid
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, RedirectResponse
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

# وضعیت جابهای در حال پردازش: job_id -> {status, archive_id, error, url}
_jobs: dict[str, dict] = {}


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


# ────────────────────────────────────────────────────────────────────────────
# تابع background آرشیو
# ────────────────────────────────────────────────────────────────────────────
async def _run_archive(job_id: str, url: str, send_telegram: bool):
    _jobs[job_id] = {"status": "running", "url": url, "archive_id": None, "error": None}
    try:
        artifact = await Archiver().archive(url)
        archive_id = await save_archive(artifact)
        artifact.archive_id = archive_id
        artifact.public_url = f"{settings.archive_base}/view/{archive_id}"

        # ارسال به تلگرام
        if send_telegram and settings.telegram_bot_token and settings.telegram_chat_id:
            try:
                TGAPI = f"https://api.telegram.org/bot{settings.telegram_bot_token}"
                target = settings.telegram_chat_id
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

        _jobs[job_id] = {"status": "done", "url": url, "archive_id": archive_id, "error": None}
        logger.info("Job %s done: %s", job_id, archive_id)

    except Exception as e:
        logger.exception("Job %s failed: %s", job_id, url)
        _jobs[job_id] = {"status": "error", "url": url, "archive_id": None, "error": str(e)[:300]}


# ────────────────────────────────────────────────────────────────────────────
# POST /archive — فوری redirect به صفحه انتظار
# ────────────────────────────────────────────────────────────────────────────
@app.post("/archive")
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

    job_id = str(uuid.uuid4())[:8]
    # آرشیو در background — بلافاصله redirect می‌کنیم
    asyncio.create_task(_run_archive(job_id, url, save_telegram))

    return RedirectResponse(f"/status/{job_id}", status_code=303)


# ────────────────────────────────────────────────────────────────────────────
# GET /status/{job_id} — صفحه انتظار با polling
# ────────────────────────────────────────────────────────────────────────────
@app.get("/status/{job_id}", response_class=HTMLResponse)
async def job_status_page(job_id: str):
    job = _jobs.get(job_id, {})
    url = job.get("url", "")
    base = settings.archive_base or ""

    page = f"""<!DOCTYPE html>
<html lang='fa' dir='rtl'>
<head>
<meta charset='UTF-8'/>
<meta name='viewport' content='width=device-width,initial-scale=1'/>
<title>آرشیو در حال پردازش</title>
<link href='https://fonts.googleapis.com/css2?family=Vazirmatn:wght@400;600;700&display=swap' rel='stylesheet'/>
<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{font-family:'Vazirmatn',sans-serif;background:#060910;color:#e2e8f0;min-height:100vh;
  display:flex;align-items:center;justify-content:center;}}
.card{{background:rgba(255,255,255,.05);border:1px solid rgba(99,102,241,.25);
  border-radius:24px;padding:48px;max-width:540px;width:90%;text-align:center;}}
.spinner{{width:56px;height:56px;border:4px solid rgba(99,102,241,.2);
  border-top-color:#6366f1;border-radius:50%;animation:spin 1s linear infinite;margin:0 auto 24px;}}
@keyframes spin{{to{{transform:rotate(360deg);}}}}
h2{{font-size:1.5rem;margin-bottom:10px;}}
.sub{{color:#64748b;font-size:.9rem;margin-bottom:24px;direction:ltr;word-break:break-all;}}
.progress{{background:rgba(255,255,255,.08);border-radius:999px;height:6px;overflow:hidden;margin:20px 0;}}
.progress-fill{{height:100%;background:linear-gradient(90deg,#6366f1,#a855f7);
  animation:progress 2s ease-in-out infinite;border-radius:999px;}}
@keyframes progress{{0%{{width:15%;margin-right:85%;}}50%{{width:70%;margin-right:0%;}}100%{{width:15%;margin-right:85%;}}}}
.result-box{{background:rgba(34,197,94,.08);border:1px solid rgba(34,197,94,.25);
  border-radius:16px;padding:20px;margin-top:20px;display:none;}}
.error-box{{background:rgba(239,68,68,.08);border:1px solid rgba(239,68,68,.25);
  border-radius:16px;padding:20px;margin-top:20px;display:none;color:#fca5a5;}}
.link-btn{{display:inline-block;margin-top:14px;padding:12px 28px;
  background:linear-gradient(90deg,#6366f1,#a855f7);color:#fff;text-decoration:none;
  border-radius:12px;font-weight:700;font-size:.95rem;}}
.back-link{{color:#6366f1;text-decoration:none;font-size:.85rem;margin-top:16px;display:block;}}
#status-text{{color:#a5b4fc;font-size:.9rem;margin-top:8px;}}
</style>
</head>
<body>
<div class='card'>
  <div id='spinner-wrap'>
    <div class='spinner'></div>
    <h2>⏳ در حال آرشیو...</h2>
    <p class='sub'>{url[:60]}{'...' if len(url)>60 else ''}</p>
    <div class='progress'><div class='progress-fill'></div></div>
    <div id='status-text'>لطفاً صبر کنید — معمولاً ۱۵-۴۵ ثانیه طول می‌کشد</div>
  </div>
  <div class='result-box' id='result-box'>
    <div style='font-size:1.4rem;margin-bottom:8px;'>✅ آرشیو شد!</div>
    <div id='result-url' style='color:#64748b;font-size:.85rem;direction:ltr;word-break:break-all;'></div>
    <a id='view-link' href='#' class='link-btn'>👁 مشاهده آرشیو</a>
  </div>
  <div class='error-box' id='error-box'>
    <div style='font-size:1.2rem;margin-bottom:8px;'>❌ خطا در آرشیو</div>
    <div id='error-msg' style='font-size:.85rem;'></div>
  </div>
  <a href='/' class='back-link'>← برگشت به صفحه اصلی</a>
</div>

<script>
const jobId = '{job_id}';
const base = '{base}';
let attempts = 0;

async function poll() {{
  attempts++;
  try {{
    const r = await fetch('/api/job/' + jobId);
    const d = await r.json();
    
    if (d.status === 'done' && d.archive_id) {{
      document.getElementById('spinner-wrap').style.display = 'none';
      const box = document.getElementById('result-box');
      box.style.display = 'block';
      document.getElementById('result-url').textContent = d.url;
      document.getElementById('view-link').href = base + '/view/' + d.archive_id;
      return;
    }}
    
    if (d.status === 'error') {{
      document.getElementById('spinner-wrap').style.display = 'none';
      const box = document.getElementById('error-box');
      box.style.display = 'block';
      document.getElementById('error-msg').textContent = d.error || 'خطای ناشناخته';
      return;
    }}
    
    // هنوز در حال پردازش
    if (attempts > 2) {{
      document.getElementById('status-text').textContent = 
        'در حال پردازش... (' + attempts*3 + ' ثانیه)';
    }}
    
    // max 3 minutes
    if (attempts < 60) {{
      setTimeout(poll, 3000);
    }} else {{
      document.getElementById('status-text').textContent = 'زمان بیش از حد طول کشید. صفحه را refresh کنید.';
    }}
  }} catch(e) {{
    if (attempts < 60) setTimeout(poll, 4000);
  }}
}}

// شروع polling بعد از 3 ثانیه
setTimeout(poll, 3000);
</script>
</body>
</html>"""
    return HTMLResponse(page)


# ────────────────────────────────────────────────────────────────────────────
# GET /api/job/{id} — وضعیت job برای polling
# ────────────────────────────────────────────────────────────────────────────
@app.get("/api/job/{job_id}")
async def get_job_status(job_id: str):
    job = _jobs.get(job_id)
    if not job:
        return JSONResponse({"status": "not_found"}, status_code=404)
    return JSONResponse(job)


# ────────────────────────────────────────────────────────────────────────────
# /view/{id}
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
                logger.info("view_archive status=%s body=%s", r.status_code, r.text[:200])
                if r.is_success and r.json():
                    row = r.json()[0]
        except Exception as e:
            logger.warning("view_archive DB error: %s", e)

    # fallback: local
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
                                   "screenshot_url": m.get("screenshot_url", ""),
                                   "html_url": ""}
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
.btn-cyan{{background:rgba(6,182,212,.15);border:1px solid rgba(6,182,212,.4);color:#67e8f9;}}
.btn-cyan:hover{{background:rgba(6,182,212,.3);}}
.btn-green{{background:rgba(34,197,94,.12);border:1px solid rgba(34,197,94,.3);color:#86efac;}}
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
# /web/{id} — HTML کامل
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
                if r.is_success and r.json():
                    html_url = r.json()[0].get("html_url", "")
        except Exception as e:
            logger.warning("web_archive DB error: %s", e)

    if html_url:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
                r2 = await c.get(html_url)
                if r2.status_code == 200:
                    return HTMLResponse(r2.text)
        except Exception as e:
            logger.warning("html fetch error: %s", e)

    # fallback: local
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
        f"<a href='/view/{archive_id}' style='color:#6366f1;margin-top:16px;display:block'>برگشت به صفحه آرشیو</a>"
        "</body></html>",
        status_code=404,
    )


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


@app.post("/bot/webhook")
async def bot_webhook(request: Request):
    try:
        update = await request.json()
    except Exception:
        return JSONResponse({"ok": False}, status_code=400)
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

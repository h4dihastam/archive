from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.services.archiver import Archiver
from app.storage.dropbox import DropboxStorageProvider
from app.storage.gdrive import GDriveStorageProvider
from app.storage.local import LocalStorageProvider
from app.storage.telegram import TelegramStorageProvider
from app.utils import is_valid_url

app = FastAPI(title=settings.app_name)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request, "result": None, "error": None})


@app.post("/archive", response_class=HTMLResponse)
async def archive(
    request: Request,
    url: str = Form(...),
    save_local: bool = Form(False),
    save_telegram: bool = Form(False),
    save_dropbox: bool = Form(False),
    save_gdrive: bool = Form(False),
):
    if not is_valid_url(url):
        return templates.TemplateResponse(
            "index.html", {"request": request, "error": "URL نامعتبر است.", "result": None}, status_code=400
        )

    archiver = Archiver()
    artifact = await archiver.archive(url)

    providers = []
    if save_local:
        providers.append(LocalStorageProvider())
    if save_telegram:
        providers.append(TelegramStorageProvider())
    if save_dropbox:
        providers.append(DropboxStorageProvider())
    if save_gdrive:
        providers.append(GDriveStorageProvider())

    uploads: dict[str, dict[str, str]] = {}
    files = {
        "raw_html": artifact.raw_html_path,
        "rendered_html": artifact.rendered_html_path,
        "screenshot": artifact.screenshot_path,
    }

    for provider in providers:
        uploads[provider.name] = {}
        for key, path in files.items():
            try:
                remote_name = f"{artifact.folder.name}_{path.name}"
                uri = await provider.upload_file(path, remote_name)
                uploads[provider.name][key] = uri
            except Exception as exc:  # noqa: BLE001
                uploads[provider.name][key] = f"ERROR: {exc}"

    manifest = {
        "url": artifact.url,
        "folder": str(Path(artifact.folder)),
        "uploads": uploads,
    }
    (artifact.folder / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "error": None,
            "result": manifest,
        },
    )

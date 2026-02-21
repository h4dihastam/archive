from __future__ import annotations

import logging
import uuid
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


class SupabaseClient:
    def __init__(self):
        self.base = settings.supabase_url.rstrip("/")
        self.key = settings.supabase_key
        self.bucket = settings.supabase_bucket
        self._headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
        }

    def _storage_url(self, path: str) -> str:
        return f"{self.base}/storage/v1/object/{self.bucket}/{path}"

    def _public_url(self, path: str) -> str:
        return f"{self.base}/storage/v1/object/public/{self.bucket}/{path}"

    def _rest_url(self, table: str) -> str:
        return f"{self.base}/rest/v1/{table}"

    async def upload(self, remote_path: str, data: bytes, content_type: str = "application/octet-stream") -> str:
        async with httpx.AsyncClient(timeout=60) as client:
            headers = {**self._headers, "Content-Type": content_type}
            res = await client.post(self._storage_url(remote_path), headers=headers, content=data)
            if res.status_code not in (200, 201):
                res2 = await client.put(self._storage_url(remote_path), headers=headers, content=data)
                if res2.status_code not in (200, 201):
                    logger.error("Storage upload failed %s: %s", res2.status_code, res2.text)
                    res2.raise_for_status()
        return self._public_url(remote_path)

    async def insert(self, table: str, row: dict) -> dict:
        async with httpx.AsyncClient(timeout=15) as client:
            headers = {
                **self._headers,
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            }
            res = await client.post(self._rest_url(table), headers=headers, json=row)
            if not res.is_success:
                logger.error("DB insert failed %s: %s", res.status_code, res.text)
                res.raise_for_status()
            return res.json()[0] if res.json() else {}

    async def select(self, table: str, filters: dict | None = None) -> list[dict]:
        params = {}
        if filters:
            for k, v in filters.items():
                params[k] = f"eq.{v}"
        async with httpx.AsyncClient(timeout=15) as client:
            headers = {**self._headers, "Accept": "application/json"}
            res = await client.get(self._rest_url(table), headers=headers, params=params)
            res.raise_for_status()
            return res.json()


_client: SupabaseClient | None = None


def get_supabase() -> SupabaseClient | None:
    if not settings.supabase_url or not settings.supabase_key:
        return None
    global _client
    if _client is None:
        _client = SupabaseClient()
    return _client


async def save_archive(artifact) -> str:
    sb = get_supabase()
    archive_id = str(uuid.uuid4())

    if not sb:
        return archive_id

    prefix = archive_id
    screenshot_url = ""
    html_url = ""
    raw_url = ""

    if artifact.screenshot_path.exists() and artifact.screenshot_path.stat().st_size > 0:
        try:
            data = artifact.screenshot_path.read_bytes()
            screenshot_url = await sb.upload(f"{prefix}/screenshot.png", data, "image/png")
        except Exception as e:
            logger.warning("Screenshot upload failed: %s", e)

    if artifact.rendered_html_path.exists():
        try:
            data = artifact.rendered_html_path.read_bytes()
            html_url = await sb.upload(f"{prefix}/archive.html", data, "text/html")
        except Exception as e:
            logger.warning("HTML upload failed: %s", e)

    if artifact.raw_html_path.exists():
        try:
            data = artifact.raw_html_path.read_bytes()
            raw_url = await sb.upload(f"{prefix}/raw.html", data, "text/html")
        except Exception as e:
            logger.warning("Raw upload failed: %s", e)

    row = {
        "id": archive_id,
        "url": artifact.url,
        "created_at": artifact.created_at.isoformat(),
        "screenshot_url": screenshot_url,
        "html_url": html_url,
        "raw_url": raw_url,
        "title": "",
        "folder": str(artifact.folder),
    }
    
    try:
        await sb.insert("archives", row)
    except Exception as e:
        logger.error("DB insert error: %s", e)
        # برگردون archive_id حتی اگه DB fail شد
        # فایل‌ها توی Storage آپلود شدن

    return archive_id

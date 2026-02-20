from pathlib import Path

import httpx

from app.config import settings
from app.storage.base import StorageProvider


class GDriveStorageProvider(StorageProvider):
    name = "gdrive"

    async def upload_file(self, local_path: Path, remote_name: str) -> str:
        if not settings.gdrive_access_token or not settings.gdrive_folder_id:
            raise RuntimeError("Google Drive is not configured")

        metadata = {
            "name": remote_name,
            "parents": [settings.gdrive_folder_id],
        }

        headers = {"Authorization": f"Bearer {settings.gdrive_access_token}"}
        files = {
            "metadata": (None, __import__("json").dumps(metadata), "application/json"),
            "file": (remote_name, local_path.open("rb"), "application/octet-stream"),
        }
        async with httpx.AsyncClient(timeout=40) as client:
            res = await client.post(
                "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart",
                headers=headers,
                files=files,
            )
            res.raise_for_status()
            payload = res.json()

        return f"gdrive://{payload.get('id', remote_name)}"

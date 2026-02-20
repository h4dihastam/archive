from pathlib import Path

import httpx

from app.config import settings
from app.storage.base import StorageProvider


class DropboxStorageProvider(StorageProvider):
    name = "dropbox"

    async def upload_file(self, local_path: Path, remote_name: str) -> str:
        if not settings.dropbox_access_token:
            raise RuntimeError("Dropbox is not configured")

        dropbox_path = f"{settings.dropbox_root_path.rstrip('/')}/{remote_name}"
        headers = {
            "Authorization": f"Bearer {settings.dropbox_access_token}",
            "Dropbox-API-Arg": (
                '{"path": "' + dropbox_path + '", "mode": "overwrite", "autorename": false, "mute": true}'
            ),
            "Content-Type": "application/octet-stream",
        }

        async with httpx.AsyncClient(timeout=30) as client:
            content = local_path.read_bytes()
            res = await client.post("https://content.dropboxapi.com/2/files/upload", headers=headers, content=content)
            res.raise_for_status()

        return f"dropbox://{dropbox_path}"

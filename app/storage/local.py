from pathlib import Path

from app.storage.base import StorageProvider


class LocalStorageProvider(StorageProvider):
    name = "local"

    async def upload_file(self, local_path: Path, remote_name: str) -> str:
        return str(local_path.resolve())

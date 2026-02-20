from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class StorageProvider(ABC):
    name: str

    @abstractmethod
    async def upload_file(self, local_path: Path, remote_name: str) -> str:
        raise NotImplementedError

from pathlib import Path

import httpx

from app.config import settings
from app.storage.base import StorageProvider


class TelegramStorageProvider(StorageProvider):
    name = "telegram"

    async def upload_file(self, local_path: Path, remote_name: str) -> str:
        if not settings.telegram_bot_token or not settings.telegram_chat_id:
            raise RuntimeError("Telegram is not configured")

        method = "sendPhoto" if local_path.suffix.lower() in {".png", ".jpg", ".jpeg"} else "sendDocument"
        file_key = "photo" if method == "sendPhoto" else "document"
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}"

        async with httpx.AsyncClient(timeout=30) as client:
            with local_path.open("rb") as f:
                files = {file_key: (remote_name, f)}
                data = {"chat_id": settings.telegram_chat_id, "caption": remote_name}
                res = await client.post(url, data=data, files=files)
                res.raise_for_status()
                payload = res.json()

        if not payload.get("ok"):
            raise RuntimeError(f"Telegram API error: {payload}")

        return f"telegram://chat/{settings.telegram_chat_id}/{remote_name}"

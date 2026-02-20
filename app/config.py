from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Archive Hub"
    base_storage_dir: str = "./data"
    request_timeout: int = 30
    playwright_timeout_ms: int = 30000

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    dropbox_access_token: str = ""
    dropbox_root_path: str = "/archive-hub"

    gdrive_access_token: str = ""
    gdrive_folder_id: str = ""


settings = Settings()

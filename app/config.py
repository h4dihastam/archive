from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Archive Hub"
    base_storage_dir: str = "./data"
    request_timeout: int = 30
    playwright_timeout_ms: int = 30000

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    webhook_url: str = ""
    public_base_url: str = ""

    bot_password: str = ""
    admin_user_id: int = 6268682882

    screenshot_machine_key: str = "dd29ad"

    supabase_url: str = ""
    supabase_key: str = ""
    supabase_bucket: str = "archives"

    x_cookies: str = ""

    @property
    def archive_base(self) -> str:
        return (self.public_base_url or self.webhook_url).rstrip("/")


settings = Settings()

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Archive Hub"
    base_storage_dir: str = "./data"
    request_timeout: int = 30
    playwright_timeout_ms: int = 30000

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Full URL of deployed app for webhook registration
    webhook_url: str = ""

    bot_password: str = "changeme"
    admin_user_id: int = 6268682882

    # Supabase
    supabase_url: str = ""
    supabase_key: str = ""        # anon/service_role key
    supabase_bucket: str = "archives"

    # Public base URL for archive links (defaults to webhook_url)
    public_base_url: str = ""

    @property
    def archive_base(self) -> str:
        return (self.public_base_url or self.webhook_url).rstrip("/")


settings = Settings()

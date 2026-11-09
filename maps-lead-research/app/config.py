from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SUPER_ADMIN_ID = 5320066517


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_path: str = str(ROOT / "data" / "app.db")

    telegram_bot_token: str = ""
    telegram_admin_ids: str = ""
    super_admin_telegram_id: int = DEFAULT_SUPER_ADMIN_ID

    google_places_api_key: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_api_base: str = "https://api.openai.com/v1"

    job_request_delay_seconds: float = 1.2
    job_max_results: int = 40
    job_max_pages: int = 3

    @property
    def telegram_allowlist(self) -> set[int]:
        ids: set[int] = set()
        for part in self.telegram_admin_ids.split(","):
            part = part.strip()
            if part:
                ids.add(int(part))
        return ids

    @property
    def super_admin_ids(self) -> set[int]:
        ids = {int(self.super_admin_telegram_id)}
        ids |= self.telegram_allowlist
        return ids

    @property
    def places_configured(self) -> bool:
        return bool(self.google_places_api_key.strip())

    @property
    def telegram_enabled(self) -> bool:
        token = self.telegram_bot_token.strip()
        return bool(token) and token.lower() not in {"changeme", "demo", "your-token"}


@lru_cache
def get_settings() -> Settings:
    return Settings()

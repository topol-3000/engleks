from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRETS_DIR = Path("/run/secrets")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BOT_",
        env_file=".env",
        extra="ignore",
        # Only point at /run/secrets when it exists, so local runs don't warn.
        secrets_dir=_SECRETS_DIR if _SECRETS_DIR.is_dir() else None,
    )

    telegram_token: SecretStr
    service_token: SecretStr
    api_base_url: str = "http://localhost:8000"
    process_timeout_s: float = 130
    log_format: Literal["json", "console"] = "json"
    log_level: str = "INFO"

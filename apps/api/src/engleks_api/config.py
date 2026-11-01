from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

_SECRETS_DIR = Path("/run/secrets")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="API_",
        env_file=".env",
        extra="ignore",
        # Only point at /run/secrets when it exists, so local runs don't warn.
        secrets_dir=_SECRETS_DIR if _SECRETS_DIR.is_dir() else None,
    )

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "engleks"
    db_user: str = "engleks"
    db_password: SecretStr
    # Shared with the bot. Never empty, or an empty X-Bot-Token would authenticate.
    service_token: SecretStr = Field(min_length=1)
    # The LiteLLM proxy's OpenAI-compatible endpoint and the API's virtual key.
    llm_base_url: str = "http://localhost:4000/v1"
    llm_model: str = "engleks-rewrite"
    llm_api_key: SecretStr
    llm_timeout_s: float = 60
    log_format: Literal["json", "console"] = "json"
    log_level: str = "INFO"

    def database_url(self) -> str:
        return URL.create(
            "postgresql+asyncpg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        ).render_as_string(hide_password=False)

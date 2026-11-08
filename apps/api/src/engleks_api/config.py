from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRETS_DIR = Path("/run/secrets")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="API_",
        env_file=".env",
        extra="ignore",
        # Validation errors would otherwise print the connection string, password included.
        hide_input_in_errors=True,
        # Only point at /run/secrets when it exists, so local runs don't warn.
        secrets_dir=_SECRETS_DIR if _SECRETS_DIR.is_dir() else None,
    )

    # postgresql+asyncpg://user:password@host:port/db, with the password percent-encoded.
    db_connection_string: SecretStr
    # Shared with the bot. Never empty, or an empty X-Bot-Token would authenticate.
    service_token: SecretStr = Field(min_length=1)
    # The LiteLLM proxy's OpenAI-compatible endpoint and the API's virtual key.
    llm_base_url: str = "http://localhost:4000/v1"
    llm_model: str = "engleks-rewrite"
    llm_api_key: SecretStr
    llm_timeout_s: float = 60
    # Per-user defaults; a user's own overrides take precedence.
    rate_limit_per_minute: int = Field(default=10, ge=1)
    daily_token_budget: int = Field(default=200000, ge=1)
    # The bot's @username without the @; lets the admin CLI print a t.me deep link.
    bot_username: str | None = None
    log_format: Literal["json", "console"] = "json"
    log_level: str = "INFO"

    @field_validator("db_connection_string")
    @classmethod
    def _requires_asyncpg(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().startswith("postgresql+asyncpg://"):
            raise ValueError("must start with postgresql+asyncpg://")
        return value

    def database_url(self) -> str:
        return self.db_connection_string.get_secret_value()

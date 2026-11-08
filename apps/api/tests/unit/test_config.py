# pyright: reportCallIssue=false
# Pyright cannot see pydantic-settings init options (_env_file, _secrets_dir).
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import make_url

from engleks_api.config import Settings

DB_URL = "postgresql+asyncpg://app:p%40ss@db:5433/engleks"
REQUIRED = {
    "db_connection_string": SecretStr(DB_URL),
    "service_token": SecretStr("tok"),
    "llm_api_key": SecretStr("key"),
}


def test_database_url_comes_from_the_connection_string():
    url = make_url(Settings(**REQUIRED, _env_file=None).database_url())
    assert (url.drivername, url.host, url.port, url.password, url.database) == (
        "postgresql+asyncpg",
        "db",
        5433,
        "p@ss",
        "engleks",
    )


@pytest.mark.filterwarnings("error")
def test_missing_connection_string_fails(monkeypatch):
    monkeypatch.delenv("API_DB_CONNECTION_STRING", raising=False)
    with pytest.raises(ValidationError, match="db_connection_string"):
        Settings(service_token=SecretStr("t"), llm_api_key=SecretStr("k"), _env_file=None)


@pytest.mark.parametrize(
    "value",
    [
        "postgresql://app:pw@db/engleks",
        "postgres://app:pw@db/engleks",
        "postgresql+psycopg://app:pw@db/engleks",
        "",
    ],
)
def test_connection_string_must_use_asyncpg(monkeypatch, value):
    monkeypatch.setenv("API_DB_CONNECTION_STRING", value)
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg://"):
        Settings(service_token=SecretStr("t"), llm_api_key=SecretStr("k"), _env_file=None)


def test_rejected_connection_string_stays_out_of_the_error(monkeypatch):
    monkeypatch.setenv("API_DB_CONNECTION_STRING", "postgres://app:hunter2@db/engleks")
    with pytest.raises(ValidationError, match=r"postgresql\+asyncpg://") as exc:
        Settings(service_token=SecretStr("t"), llm_api_key=SecretStr("k"), _env_file=None)
    assert "hunter2" not in str(exc.value)


def test_reads_secret_file(tmp_path, monkeypatch):
    for name in ("API_DB_CONNECTION_STRING", "API_SERVICE_TOKEN", "API_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "api_db_connection_string").write_text(DB_URL)
    (tmp_path / "api_service_token").write_text("token-file")
    (tmp_path / "api_llm_api_key").write_text("key-file")
    s = Settings(_env_file=None, _secrets_dir=tmp_path)
    assert s.db_connection_string.get_secret_value() == DB_URL
    assert s.service_token.get_secret_value() == "token-file"
    assert s.llm_api_key.get_secret_value() == "key-file"


def test_service_token_must_not_be_empty():
    with pytest.raises(ValidationError, match="service_token"):
        Settings(
            db_connection_string=SecretStr(DB_URL),
            service_token=SecretStr(""),
            llm_api_key=SecretStr("k"),
            _env_file=None,
        )


def test_llm_defaults(monkeypatch):
    for name in ("API_LLM_BASE_URL", "API_LLM_MODEL", "API_LLM_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(**REQUIRED, _env_file=None)
    assert (s.llm_base_url, s.llm_model, s.llm_timeout_s) == (
        "http://localhost:4000/v1",
        "engleks-rewrite",
        60,
    )


def test_limit_defaults(monkeypatch):
    for name in ("API_RATE_LIMIT_PER_MINUTE", "API_DAILY_TOKEN_BUDGET"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(**REQUIRED, _env_file=None)
    assert (s.rate_limit_per_minute, s.daily_token_budget) == (10, 200000)


def test_bot_username_defaults_to_none(monkeypatch):
    monkeypatch.delenv("API_BOT_USERNAME", raising=False)
    s = Settings(**REQUIRED, _env_file=None)
    assert s.bot_username is None

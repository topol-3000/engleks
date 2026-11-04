# pyright: reportCallIssue=false
# Pyright cannot see pydantic-settings init options (_env_file, _secrets_dir).
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import make_url

from engleks_api.config import Settings

REQUIRED = {"service_token": SecretStr("tok"), "llm_api_key": SecretStr("key")}


def test_database_url_escapes_password():
    s = Settings(db_password=SecretStr("p@ss:/w"), db_host="db", **REQUIRED, _env_file=None)
    url = make_url(s.database_url())
    assert (url.drivername, url.host, url.password) == ("postgresql+asyncpg", "db", "p@ss:/w")


@pytest.mark.filterwarnings("error")
def test_missing_db_password_fails(monkeypatch):
    monkeypatch.delenv("API_DB_PASSWORD", raising=False)
    with pytest.raises(ValidationError, match="db_password"):
        Settings(**REQUIRED, _env_file=None)


def test_reads_secret_file(tmp_path, monkeypatch):
    for name in ("API_DB_PASSWORD", "API_SERVICE_TOKEN", "API_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    (tmp_path / "api_db_password").write_text("from-file")
    (tmp_path / "api_service_token").write_text("token-file")
    (tmp_path / "api_llm_api_key").write_text("key-file")
    s = Settings(_env_file=None, _secrets_dir=tmp_path)
    assert s.db_password.get_secret_value() == "from-file"
    assert s.service_token.get_secret_value() == "token-file"
    assert s.llm_api_key.get_secret_value() == "key-file"


def test_service_token_must_not_be_empty():
    with pytest.raises(ValidationError, match="service_token"):
        Settings(
            db_password=SecretStr("x"),
            service_token=SecretStr(""),
            llm_api_key=SecretStr("k"),
            _env_file=None,
        )


def test_llm_defaults(monkeypatch):
    for name in ("API_LLM_BASE_URL", "API_LLM_MODEL", "API_LLM_TIMEOUT_S"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(db_password=SecretStr("x"), **REQUIRED, _env_file=None)
    assert (s.llm_base_url, s.llm_model, s.llm_timeout_s) == (
        "http://localhost:4000/v1",
        "engleks-rewrite",
        60,
    )


def test_limit_defaults(monkeypatch):
    for name in ("API_RATE_LIMIT_PER_MINUTE", "API_DAILY_TOKEN_BUDGET"):
        monkeypatch.delenv(name, raising=False)
    s = Settings(db_password=SecretStr("x"), **REQUIRED, _env_file=None)
    assert (s.rate_limit_per_minute, s.daily_token_budget) == (10, 200000)

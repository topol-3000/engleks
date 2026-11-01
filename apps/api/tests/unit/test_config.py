# pyright: reportCallIssue=false
# Pyright cannot see pydantic-settings init options (_env_file, _secrets_dir).
import pytest
from pydantic import SecretStr, ValidationError
from sqlalchemy import make_url

from engleks_api.config import Settings


def test_database_url_escapes_password():
    s = Settings(db_password=SecretStr("p@ss:/w"), db_host="db", _env_file=None)
    url = make_url(s.database_url())
    assert (url.drivername, url.host, url.password) == ("postgresql+asyncpg", "db", "p@ss:/w")


@pytest.mark.filterwarnings("error")
def test_missing_db_password_fails(monkeypatch):
    monkeypatch.delenv("API_DB_PASSWORD", raising=False)
    with pytest.raises(ValidationError, match="db_password"):
        Settings(_env_file=None)


def test_reads_secret_file(tmp_path, monkeypatch):
    monkeypatch.delenv("API_DB_PASSWORD", raising=False)
    (tmp_path / "api_db_password").write_text("from-file")
    s = Settings(_env_file=None, _secrets_dir=tmp_path)
    assert s.db_password.get_secret_value() == "from-file"

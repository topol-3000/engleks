# pyright: reportCallIssue=false
# Pyright cannot see pydantic-settings init options (_env_file, _secrets_dir).
import pytest
from pydantic import ValidationError

from engleks_bot.config import Settings


@pytest.mark.filterwarnings("error")
def test_missing_telegram_token_fails(monkeypatch):
    monkeypatch.delenv("BOT_TELEGRAM_TOKEN", raising=False)
    monkeypatch.setenv("BOT_SERVICE_TOKEN", "tok")
    with pytest.raises(ValidationError, match="telegram_token"):
        Settings(_env_file=None)

# pyright: reportCallIssue=false
# Pyright cannot see pydantic-settings init options (_env_file, _secrets_dir).
import socket
import time

from pydantic import SecretStr

from engleks_api.config import Settings
from engleks_api.main import create_app


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def test_readyz_ok(pg_settings, client_for):
    async with client_for(create_app(pg_settings)) as c:
        r = await c.get("/readyz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


async def test_readyz_db_unreachable(client_for):
    settings = Settings(
        db_password=SecretStr("x"),
        db_port=free_port(),
        service_token=SecretStr("t"),
        llm_api_key=SecretStr("unused"),
        _env_file=None,
    )
    start = time.monotonic()
    async with client_for(create_app(settings)) as c:
        r = await c.get("/readyz")
    assert r.status_code == 503 and r.json() == {"status": "unavailable"}
    assert time.monotonic() - start < 5

from collections.abc import AsyncIterator
from decimal import Decimal

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker
from structlog.testing import capture_logs

from engleks_api.domain.access import generate_invite_code
from engleks_api.domain.text_processing import Language, RewriteOutcome, Usage
from engleks_api.infra.db.repositories import SqlInviteRepository
from engleks_api.main import create_app
from engleks_api.transport.texts import text_rewriter

URL = "/v1/auth/redeem"
PROCESS = "/v1/texts/process"
UNAUTHORIZED = {"code": "unauthorized", "message": "Unauthorized"}


class FakeRewriter:
    async def rewrite(self, text: str) -> RewriteOutcome:
        usage = Usage("ollama_chat/gemma3:4b", "process_text.v5", 10, 5, Decimal("0.0001"))
        return RewriteOutcome(Language.EN, "He goes home.", ["Add -s."], usage, {})


def headers(user_id: str | None = "4242", token: str | None = "test-service-token") -> dict:
    h = {}
    if user_id is not None:
        h["X-Telegram-User-Id"] = user_id
    if token is not None:
        h["X-Bot-Token"] = token
    return h


@pytest.fixture
async def api(pg_settings, migrated_url, client_for) -> AsyncIterator[httpx2.AsyncClient]:
    app = create_app(pg_settings)
    app.dependency_overrides[text_rewriter] = lambda: FakeRewriter()
    async with client_for(app) as client:
        yield client


def sessionmaker(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def new_code(engine, max_uses: int = 1) -> str:
    code = generate_invite_code()
    await SqlInviteRepository(sessionmaker(engine)).create(code, None, max_uses, None)
    return code


async def used_count(engine, code: str) -> int:
    async with engine.connect() as c:
        return await c.scalar(
            text("SELECT used_count FROM invite_code WHERE code = :c"), {"c": code}
        )


async def test_redeem_registers_then_process_works(api, engine):
    code = await new_code(engine)
    r = await api.post(URL, json={"code": code}, headers=headers("5151"))
    assert (r.status_code, r.json()) == (200, {"status": "registered"})
    r = await api.post(PROCESS, json={"text": "He go home."}, headers=headers("5151"))
    assert r.status_code == 200


async def test_redeem_twice_already_registered(api, engine):
    code = await new_code(engine, max_uses=3)
    await api.post(URL, json={"code": code}, headers=headers("5252"))
    r = await api.post(URL, json={"code": code}, headers=headers("5252"))
    assert (r.status_code, r.json()) == (200, {"status": "already_registered"})
    assert await used_count(engine, code) == 1


async def test_lowercase_code_is_accepted(api, engine):
    code = await new_code(engine)
    r = await api.post(URL, json={"code": code.lower()}, headers=headers("5353"))
    assert (r.status_code, r.json()) == (200, {"status": "registered"})


async def test_unknown_code_400(api):
    r = await api.post(URL, json={"code": "NOSUCHCODE22"}, headers=headers("5454"))
    assert (r.status_code, r.json()) == (
        400,
        {"code": "invalid_invite", "message": "Invalid invite"},
    )


async def test_disabled_user_403(api, engine, register):
    _, ext = await register(sessionmaker(engine), status="disabled")
    code = await new_code(engine)
    r = await api.post(URL, json={"code": code}, headers=headers(ext))
    assert (r.status_code, r.json()["code"]) == (403, "user_disabled")
    assert await used_count(engine, code) == 0


@pytest.mark.parametrize("code", ["", "A" * 65])
async def test_bad_code_422(api, code):
    r = await api.post(URL, json={"code": code}, headers=headers("5555"))
    assert (r.status_code, r.json()["code"]) == (422, "validation_error")


async def test_missing_token_401(api):
    r = await api.post(URL, json={"code": "ABC"}, headers=headers(token=None))
    assert (r.status_code, r.json()) == (401, UNAUTHORIZED)


async def test_bad_user_id_401(api):
    r = await api.post(URL, json={"code": "ABC"}, headers=headers("007"))
    assert (r.status_code, r.json()) == (401, UNAUTHORIZED)


async def test_rejection_logged_without_code(api):
    code = "NOSUCHCODE22"
    with capture_logs() as logs:
        await api.post(URL, json={"code": code}, headers=headers("5656"))
    [entry] = [e for e in logs if e["event"] == "invite_rejected"]
    assert entry["reason"] == "unknown"
    assert not any(code in str(v) for e in logs for v in e.values())


async def test_openapi_declares_error_responses(app):
    responses = app.openapi()["paths"][URL]["post"]["responses"]
    assert {"400", "401", "403", "422"} <= responses.keys()

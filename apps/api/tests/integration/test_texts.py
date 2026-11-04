from collections.abc import AsyncIterator
from decimal import Decimal

import httpx2
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from engleks_api.domain.text_processing import (
    Language,
    RewriteFailed,
    RewriteOutcome,
    Usage,
)
from engleks_api.main import create_app
from engleks_api.transport.texts import text_rewriter
from engleks_contracts import ProcessTextResponse

URL = "/v1/texts/process"
UNAUTHORIZED = {"code": "unauthorized", "message": "Unauthorized"}


class FakeRewriter:
    def __init__(self) -> None:
        self.outcome = RewriteOutcome(
            source_language=Language.EN,
            output_text="He goes home.",
            explanations=["Add -s to the verb after he, she or it."],
            usage=Usage("ollama_chat/gemma3:4b", "process_text.v5", 10, 5, Decimal("0.0001")),
            llm_response={"source_language": "en"},
        )
        self.fail_with: RewriteFailed | None = None
        self.texts: list[str] = []

    async def rewrite(self, text: str) -> RewriteOutcome:
        self.texts.append(text)
        if self.fail_with is not None:
            raise self.fail_with
        return self.outcome


def headers(user_id: str | None = "4242", token: str | None = "test-service-token") -> dict:
    h = {}
    if user_id is not None:
        h["X-Telegram-User-Id"] = user_id
    if token is not None:
        h["X-Bot-Token"] = token
    return h


@pytest.fixture
async def api(
    pg_settings, migrated_url, client_for
) -> AsyncIterator[tuple[httpx2.AsyncClient, FakeRewriter]]:
    app = create_app(pg_settings)
    fake = FakeRewriter()
    app.dependency_overrides[text_rewriter] = lambda: fake
    async with client_for(app) as client:
        yield client, fake


def users(engine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def count_interactions(engine, external_id: str) -> int:
    async with engine.connect() as c:
        return await c.scalar(
            text(
                "SELECT count(*) FROM interaction JOIN identity USING (user_id) "
                "WHERE identity.external_id = :e"
            ),
            {"e": external_id},
        )


async def test_missing_token_401(api):
    client, fake = api
    r = await client.post(URL, json={"text": "hi"}, headers=headers(token=None))
    assert (r.status_code, r.json()) == (401, UNAUTHORIZED)
    assert fake.texts == []


@pytest.mark.parametrize("token", ["wrong", ""])
async def test_wrong_token_401(api, token):
    client, fake = api
    r = await client.post(
        URL, json={"text": "hi"}, headers={"X-Telegram-User-Id": "4242", "X-Bot-Token": token}
    )
    assert (r.status_code, r.json()) == (401, UNAUTHORIZED)
    assert fake.texts == []


async def test_non_ascii_token_bytes_401(api):
    client, _ = api
    h = [(b"X-Telegram-User-Id", b"4242"), (b"X-Bot-Token", "tök".encode())]
    r = await client.post(URL, json={"text": "hi"}, headers=h)
    assert (r.status_code, r.json()) == (401, UNAUTHORIZED)


@pytest.mark.parametrize("user_id", [None, "", "abc", "0", "-5", "007", "+7", "1.5", "9" * 20])
async def test_bad_user_id_401(api, engine, user_id):
    client, fake = api
    r = await client.post(URL, json={"text": "hi"}, headers=headers(user_id))
    assert (r.status_code, r.json()) == (401, UNAUTHORIZED)
    assert fake.texts == []
    async with engine.connect() as c:
        stored = await c.scalar(
            text("SELECT count(*) FROM identity WHERE external_id = :e"), {"e": user_id or ""}
        )
    assert stored == 0


async def test_blank_text_422(api, engine, register):
    client, _ = api
    # FastAPI authenticates before it reports body errors, so the caller must exist.
    _, ext = await register(users(engine))
    r = await client.post(URL, json={"text": "   "}, headers=headers(ext))
    assert (r.status_code, r.json()["code"]) == (422, "validation_error")


async def test_llm_failure_502_saves_nothing(api, engine, register):
    client, fake = api
    _, ext = await register(users(engine))
    fake.fail_with = RewriteFailed("timeout")
    r = await client.post(URL, json={"text": "He go home."}, headers=headers(ext))
    assert (r.status_code, r.json()) == (
        502,
        {"code": "llm_unavailable", "message": "LLM unavailable"},
    )
    assert await count_interactions(engine, ext) == 0


async def test_success_returns_explanations_and_saves_interaction(api, engine, register):
    client, fake = api
    _, ext = await register(users(engine))
    r = await client.post(URL, json={"text": "He go home."}, headers=headers(ext))
    assert r.status_code == 200
    assert "mode" not in r.json()
    body = ProcessTextResponse.model_validate(r.json())
    assert body.output_text == "He goes home."
    assert body.explanations == ["Add -s to the verb after he, she or it."]
    assert fake.texts == ["He go home."]
    assert await count_interactions(engine, ext) == 1


async def test_old_client_fields_are_ignored(api, engine, register):
    client, fake = api
    _, ext = await register(users(engine))
    body = {"text": "He go home.", "mode": "minimal", "explanation_language": "uk"}
    r = await client.post(URL, json=body, headers=headers(ext))
    assert r.status_code == 200
    assert fake.texts == ["He go home."]


async def test_unknown_identity_401_invite_required(api, engine):
    client, fake = api
    r = await client.post(URL, json={"text": "hi"}, headers=headers("909090"))
    assert (r.status_code, r.json()) == (
        401,
        {"code": "invite_required", "message": "Invite required"},
    )
    assert fake.texts == []
    async with engine.connect() as c:
        stored = await c.scalar(text("SELECT count(*) FROM identity WHERE external_id = '909090'"))
    assert stored == 0


async def test_disabled_user_403(api, engine, register):
    client, fake = api
    _, ext = await register(users(engine), status="disabled")
    r = await client.post(URL, json={"text": "hi"}, headers=headers(ext))
    assert (r.status_code, r.json()) == (
        403,
        {"code": "user_disabled", "message": "User disabled"},
    )
    assert fake.texts == []


async def test_openapi_declares_error_responses(app):
    responses = app.openapi()["paths"][URL]["post"]["responses"]
    assert {"401", "403", "422", "429", "502"} <= responses.keys()


async def usage_row(engine, user_id):
    async with engine.connect() as c:
        return (
            await c.execute(
                text("SELECT requests, tokens_in, tokens_out FROM usage_daily WHERE user_id = :u"),
                {"u": user_id},
            )
        ).one_or_none()


async def test_rate_limit_429(api, register, engine):
    client, fake = api
    _, ext = await register(users(engine), rate_limit_per_minute=1)
    first = await client.post(URL, json={"text": "a"}, headers=headers(ext))
    assert first.status_code == 200
    r = await client.post(URL, json={"text": "b"}, headers=headers(ext))
    assert (r.status_code, r.json()) == (
        429,
        {"code": "rate_limited", "message": "Too many requests"},
    )
    assert len(fake.texts) == 1


async def test_daily_budget_429(api, register, engine):
    client, fake = api
    _, ext = await register(users(engine), daily_token_budget=15)
    first = await client.post(URL, json={"text": "a"}, headers=headers(ext))
    assert first.status_code == 200
    r = await client.post(URL, json={"text": "b"}, headers=headers(ext))
    assert (r.status_code, r.json()) == (
        429,
        {"code": "daily_budget", "message": "Daily budget exhausted"},
    )
    assert len(fake.texts) == 1


async def test_llm_failure_records_usage(api, register, engine):
    client, fake = api
    user_id, ext = await register(users(engine))
    fake.fail_with = RewriteFailed(
        "invalid_output", usage=Usage("m", "process_text.v5", 7, 3, None)
    )
    r = await client.post(URL, json={"text": "a"}, headers=headers(ext))
    assert r.status_code == 502
    assert tuple(await usage_row(engine, user_id)) == (1, 7, 3)
    assert await count_interactions(engine, ext) == 0

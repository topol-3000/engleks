import json
from collections.abc import Callable
from uuid import UUID

import httpx2
import pytest

from engleks_bot.api_client import ApiClient, ApiError
from engleks_contracts import (
    ErrorCode,
    ErrorResponse,
    HealthResponse,
    HealthStatus,
    Language,
    ProcessTextRequest,
    ProcessTextResponse,
    RedeemInviteRequest,
    RedeemInviteResponse,
    RedeemStatus,
)


def client_for(handler: Callable[[httpx2.Request], httpx2.Response]) -> ApiClient:
    return ApiClient("http://api", "tok", transport=httpx2.MockTransport(handler))


async def test_health_ok_sends_bot_token():
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        body = HealthResponse(status=HealthStatus.OK).model_dump(mode="json")
        return httpx2.Response(200, json=body)

    async with client_for(handler) as c:
        assert (await c.health()).status is HealthStatus.OK
    assert (seen[-1].method, seen[-1].url.path) == ("GET", "/healthz")
    assert seen[-1].headers["X-Bot-Token"] == "tok"


async def test_error_response_parsed():
    body = ErrorResponse(code=ErrorCode.INTERNAL_ERROR, message="x").model_dump(mode="json")
    async with client_for(lambda _: httpx2.Response(500, json=body)) as c:
        with pytest.raises(ApiError) as e:
            await c.health()
    assert (e.value.status_code, e.value.code) == (500, ErrorCode.INTERNAL_ERROR)


@pytest.mark.parametrize(
    "response",
    [
        httpx2.Response(502, text="<html>Bad Gateway</html>"),
        httpx2.Response(404, json={"detail": "Not Found"}),
        httpx2.Response(429, json={"code": "some_future_code", "message": "x"}),
    ],
)
async def test_unexpected_error_body_gives_code_none(response):
    async with client_for(lambda _: response) as c:
        with pytest.raises(ApiError) as e:
            await c.health()
    assert (e.value.status_code, e.value.code) == (response.status_code, None)


async def test_transport_failure():
    def down(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down")

    async with client_for(down) as c:
        with pytest.raises(ApiError) as e:
            await c.health()
    assert (e.value.status_code, e.value.code) == (None, None)


async def test_invalid_success_body():
    async with client_for(lambda _: httpx2.Response(200, text="not json")) as c:
        with pytest.raises(ApiError) as e:
            await c.health()
    assert (e.value.status_code, e.value.code) == (200, None)


def health_body() -> dict[str, object]:
    return HealthResponse(status=HealthStatus.OK).model_dump(mode="json")


def ok_response() -> ProcessTextResponse:
    return ProcessTextResponse(
        interaction_id=UUID(int=1),
        source_language=Language.EN,
        output_text="I went.",
        explanations=[],
    )


async def test_process_text_request_and_response():
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=ok_response().model_dump(mode="json"))

    async with client_for(handler) as c:
        got = await c.process_text(42, ProcessTextRequest(text="I has went."))
    assert got == ok_response()
    r = seen[-1]
    assert (r.method, r.url.path) == ("POST", "/v1/texts/process")
    assert (r.headers["X-Bot-Token"], r.headers["X-Telegram-User-Id"]) == ("tok", "42")
    assert json.loads(r.content) == {"text": "I has went."}


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, ErrorCode.UNAUTHORIZED),
        (422, ErrorCode.VALIDATION_ERROR),
        (502, ErrorCode.LLM_UNAVAILABLE),
    ],
)
async def test_process_text_errors(status, code):
    body = ErrorResponse(code=code, message="x").model_dump(mode="json")
    async with client_for(lambda _: httpx2.Response(status, json=body)) as c:
        with pytest.raises(ApiError) as e:
            await c.process_text(1, ProcessTextRequest(text="hi"))
    assert (e.value.status_code, e.value.code) == (status, code)


async def test_process_timeout_only_on_process_text():
    timeouts: dict[str, dict] = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        timeouts[request.url.path] = request.extensions["timeout"]
        if request.url.path == "/healthz":
            return httpx2.Response(200, json=health_body())
        return httpx2.Response(200, json=ok_response().model_dump(mode="json"))

    async with client_for(handler) as c:
        await c.health()
        await c.process_text(1, ProcessTextRequest(text="hi"))
    assert set(timeouts["/healthz"].values()) == {10.0}
    assert set(timeouts["/v1/texts/process"].values()) == {130.0}


async def test_health_has_no_user_header():
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.url.path == "/healthz":
            return httpx2.Response(200, json=health_body())
        return httpx2.Response(200, json=ok_response().model_dump(mode="json"))

    async with client_for(handler) as c:
        await c.process_text(1, ProcessTextRequest(text="hi"))
        await c.health()
    assert seen[0].headers["X-Telegram-User-Id"] == "1"
    assert "X-Telegram-User-Id" not in seen[-1].headers


async def test_redeem_request_and_response():
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        body = RedeemInviteResponse(status=RedeemStatus.REGISTERED)
        return httpx2.Response(200, json=body.model_dump(mode="json"))

    async with client_for(handler) as c:
        got = await c.redeem_invite(42, RedeemInviteRequest(code="ABC"))
    assert got == RedeemInviteResponse(status=RedeemStatus.REGISTERED)
    r = seen[-1]
    assert (r.method, r.url.path) == ("POST", "/v1/auth/redeem")
    assert (r.headers["X-Bot-Token"], r.headers["X-Telegram-User-Id"]) == ("tok", "42")
    assert json.loads(r.content) == {"code": "ABC"}


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, ErrorCode.INVALID_INVITE), (403, ErrorCode.USER_DISABLED)],
)
async def test_redeem_errors(status, code):
    body = ErrorResponse(code=code, message="x").model_dump(mode="json")
    async with client_for(lambda _: httpx2.Response(status, json=body)) as c:
        with pytest.raises(ApiError) as e:
            await c.redeem_invite(1, RedeemInviteRequest(code="ABC"))
    assert (e.value.status_code, e.value.code) == (status, code)

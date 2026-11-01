from collections.abc import Callable

import httpx2
import pytest

from engleks_bot.api_client import ApiClient, ApiError
from engleks_contracts import ErrorCode, ErrorResponse, HealthResponse, HealthStatus


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

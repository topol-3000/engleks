import httpx
import pytest

from engleks_bot.api_client import ApiClient, ApiError
from engleks_contracts import ErrorCode, ErrorResponse, HealthResponse, HealthStatus


async def test_health_ok_sends_bot_token(respx_mock):
    route = respx_mock.get("http://api/healthz").respond(
        json=HealthResponse(status=HealthStatus.OK).model_dump(mode="json")
    )
    async with ApiClient("http://api", "tok") as c:
        assert (await c.health()).status is HealthStatus.OK
    assert route.calls.last.request.headers["X-Bot-Token"] == "tok"


async def test_error_response_parsed(respx_mock):
    respx_mock.get("http://api/healthz").respond(
        500, json=ErrorResponse(code=ErrorCode.INTERNAL_ERROR, message="x").model_dump(mode="json")
    )
    async with ApiClient("http://api", "tok") as c:
        with pytest.raises(ApiError) as e:
            await c.health()
    assert (e.value.status_code, e.value.code) == (500, ErrorCode.INTERNAL_ERROR)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(502, text="<html>Bad Gateway</html>"),
        httpx.Response(404, json={"detail": "Not Found"}),
        httpx.Response(429, json={"code": "some_future_code", "message": "x"}),
    ],
)
async def test_unexpected_error_body_gives_code_none(respx_mock, response):
    respx_mock.get("http://api/healthz").mock(return_value=response)
    async with ApiClient("http://api", "tok") as c:
        with pytest.raises(ApiError) as e:
            await c.health()
    assert (e.value.status_code, e.value.code) == (response.status_code, None)


async def test_transport_failure(respx_mock):
    respx_mock.get("http://api/healthz").mock(side_effect=httpx.ConnectError("down"))
    async with ApiClient("http://api", "tok") as c:
        with pytest.raises(ApiError) as e:
            await c.health()
    assert (e.value.status_code, e.value.code) == (None, None)


async def test_invalid_success_body(respx_mock):
    respx_mock.get("http://api/healthz").respond(200, text="not json")
    async with ApiClient("http://api", "tok") as c:
        with pytest.raises(ApiError) as e:
            await c.health()
    assert (e.value.status_code, e.value.code) == (200, None)

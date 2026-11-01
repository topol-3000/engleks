import httpx
import pytest

from engleks_bot.api_client import ApiClient, ApiError
from engleks_bot.main import check_api


async def test_check_api_ok(respx_mock):
    respx_mock.get("http://api/healthz").respond(json={"status": "ok"})
    async with ApiClient("http://api", "tok") as c:
        await check_api(c)


async def test_check_api_raises_when_down(respx_mock):
    respx_mock.get("http://api/healthz").mock(side_effect=httpx.ConnectError("down"))
    async with ApiClient("http://api", "tok") as c:
        with pytest.raises(ApiError):
            await check_api(c)

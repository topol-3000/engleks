import httpx2
import pytest

from engleks_bot.api_client import ApiClient, ApiError
from engleks_bot.main import check_api


async def test_check_api_ok():
    transport = httpx2.MockTransport(lambda _: httpx2.Response(200, json={"status": "ok"}))
    async with ApiClient("http://api", "tok", transport=transport) as c:
        await check_api(c)


async def test_check_api_raises_when_down():
    def down(_: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("down")

    async with ApiClient("http://api", "tok", transport=httpx2.MockTransport(down)) as c:
        with pytest.raises(ApiError):
            await check_api(c)

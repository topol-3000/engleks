import asyncio

import structlog
from aiogram import Bot, Dispatcher

from engleks_bot.api_client import ApiClient, ApiError
from engleks_bot.config import Settings
from engleks_bot.handlers import start
from engleks_bot.logging_setup import configure_logging

log = structlog.get_logger()


async def check_api(client: ApiClient) -> None:
    """Fail fast at startup if the API is not reachable."""
    try:
        health = await client.health()
    except ApiError as exc:
        log.error("api_unreachable", status_code=exc.status_code, code=exc.code)
        raise
    log.info("api_healthy", status=health.status)


async def run(settings: Settings) -> None:
    configure_logging(settings.log_format, settings.log_level)
    async with ApiClient(
        settings.api_base_url, settings.service_token.get_secret_value()
    ) as client:
        await check_api(client)
        dispatcher = Dispatcher()
        dispatcher.include_router(start.router)
        bot = Bot(settings.telegram_token.get_secret_value())
        await dispatcher.start_polling(bot)


def main() -> None:
    asyncio.run(run(Settings()))  # pyright: ignore[reportCallIssue]

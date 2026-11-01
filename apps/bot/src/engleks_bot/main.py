import asyncio

import structlog
from aiogram import Bot, Dispatcher, F
from aiogram.enums import ChatType

from engleks_bot.api_client import ApiClient, ApiError
from engleks_bot.config import Settings
from engleks_bot.handlers import errors, rerun, start, texts
from engleks_bot.logging_setup import configure_logging

log = structlog.stdlib.get_logger()


async def check_api(client: ApiClient) -> None:
    """Fail fast at startup if the API is not reachable."""
    try:
        health = await client.health()
    except ApiError as exc:
        log.error("api_unreachable", status_code=exc.status_code, code=exc.code)
        raise
    log.info("api_healthy", status=health.status)


def build_dispatcher(api: ApiClient) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher["api"] = api
    dispatcher["in_flight"] = set()
    dispatcher.message.filter(F.chat.type == ChatType.PRIVATE)
    dispatcher.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
    dispatcher.include_router(start.build_router())
    dispatcher.include_router(texts.build_router())
    dispatcher.include_router(rerun.build_router())
    dispatcher.errors.register(errors.on_error)
    return dispatcher


async def run(settings: Settings) -> None:
    configure_logging(settings.log_format, settings.log_level)
    async with ApiClient(
        settings.api_base_url,
        settings.service_token.get_secret_value(),
        process_timeout=settings.process_timeout_s,
    ) as client:
        await check_api(client)
        dispatcher = build_dispatcher(client)
        bot = Bot(settings.telegram_token.get_secret_value())
        await dispatcher.start_polling(bot)


def main() -> None:
    asyncio.run(run(Settings()))  # pyright: ignore[reportCallIssue]

import time

import structlog
from aiogram import Bot, F, Router
from aiogram.types import LinkPreviewOptions, Message, User
from aiogram.utils.chat_action import ChatActionSender
from aiogram.utils.formatting import Text
from pydantic import ValidationError

from engleks_bot.api_client import ApiClient, ApiError
from engleks_bot.handlers.start import HELP_TEXT
from engleks_bot.rendering.errors import (
    CANNOT_PROCESS,
    MAX_INPUT_CHARS,
    SEND_TEXT,
    TOO_LONG,
    error_text,
)
from engleks_bot.rendering.reply import render_reply
from engleks_contracts import ProcessTextRequest

log = structlog.stdlib.get_logger()


async def on_text(message: Message, event_from_user: User, api: ApiClient, bot: Bot) -> None:
    async def reply(content: Text) -> None:
        await message.answer(
            **content.as_kwargs(),
            reply_parameters=message.as_reply_parameters(allow_sending_without_reply=True),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    text = message.text or ""
    if len(text) > MAX_INPUT_CHARS:
        log.info("input_too_long", text_len=len(text), limit=MAX_INPUT_CHARS)
        await reply(Text(TOO_LONG))
        return
    try:
        request = ProcessTextRequest(text=text)
    except ValidationError:
        # Not logged: the error message would echo the user's text.
        await reply(Text(CANNOT_PROCESS))
        return

    started = time.monotonic()
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=message.chat.id):
            response = await api.process_text(event_from_user.id, request)
    except ApiError as exc:
        log.warning("api_call_failed", status_code=exc.status_code, code=exc.code)
        await reply(Text(error_text(exc)))
        return
    duration_ms = int((time.monotonic() - started) * 1000)

    parts = render_reply(response)
    log.info(
        "text_processed",
        interaction_id=str(response.interaction_id),
        source_language=response.source_language.value,
        text_len=len(text),
        explanations=len(response.explanations),
        messages=len(parts),
        duration_ms=duration_ms,
    )
    for part in parts:
        await reply(part)


async def on_command(message: Message) -> None:
    await message.answer(**Text(HELP_TEXT).as_kwargs())


async def on_other(message: Message) -> None:
    await message.answer(**Text(SEND_TEXT).as_kwargs())


def build_router() -> Router:
    router = Router()
    router.message.register(on_text, F.text & ~F.text.startswith("/"))
    router.message.register(on_command, F.text.startswith("/"))
    router.message.register(on_other)
    return router

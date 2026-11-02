import time
from collections.abc import Iterable

import structlog
from aiogram import Bot, F, Router
from aiogram.types import InlineKeyboardMarkup, LinkPreviewOptions, Message, User
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
from engleks_bot.rendering.keyboards import rerun_keyboard
from engleks_bot.rendering.reply import render_reply
from engleks_contracts import Mode, ProcessTextRequest

log = structlog.stdlib.get_logger()


async def process_and_reply(
    original: Message,
    mode: Mode,
    next_modes: Iterable[Mode],
    *,
    user_id: int,
    api: ApiClient,
    bot: Bot,
) -> bool:
    """Send the original's text to the API and reply with the result. True on success."""

    async def reply(
        content: Text,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> None:
        await original.answer(
            **content.as_kwargs(),
            reply_markup=reply_markup,
            reply_parameters=original.as_reply_parameters(allow_sending_without_reply=True),
            link_preview_options=LinkPreviewOptions(is_disabled=True),
        )

    text = original.text or ""
    if len(text) > MAX_INPUT_CHARS:
        log.info("input_too_long", text_len=len(text), limit=MAX_INPUT_CHARS)
        await reply(Text(TOO_LONG))
        return False
    try:
        request = ProcessTextRequest(text=text, mode=mode)
    except ValidationError:
        # Not logged: the error message would echo the user's text.
        await reply(Text(CANNOT_PROCESS))
        return False

    started = time.monotonic()
    try:
        async with ChatActionSender.typing(bot=bot, chat_id=original.chat.id):
            response = await api.process_text(user_id, request)
    except ApiError as exc:
        log.warning("api_call_failed", status_code=exc.status_code, code=exc.code, mode=mode.value)
        await reply(Text(error_text(exc)))
        return False
    duration_ms = int((time.monotonic() - started) * 1000)

    parts = render_reply(response)
    keyboard = rerun_keyboard(next_modes)
    log.info(
        "text_processed",
        interaction_id=str(response.interaction_id),
        mode=mode.value,
        source_language=response.source_language.value,
        text_len=len(text),
        explanations=len(response.explanations),
        messages=len(parts),
        duration_ms=duration_ms,
    )
    for i, part in enumerate(parts):
        last = i == len(parts) - 1
        await reply(part, reply_markup=keyboard if last else None)
    return True


async def on_text(message: Message, event_from_user: User, api: ApiClient, bot: Bot) -> None:
    await process_and_reply(
        message,
        Mode.NATURAL,
        [Mode.MINIMAL, Mode.POLISHED],
        user_id=event_from_user.id,
        api=api,
        bot=bot,
    )


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

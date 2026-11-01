from contextlib import suppress

import structlog
from aiogram import Bot, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from engleks_bot.api_client import ApiClient
from engleks_bot.handlers.texts import process_and_reply
from engleks_bot.rendering.errors import ALREADY_WORKING, ORIGINAL_GONE, TOO_OLD, rewriting
from engleks_bot.rendering.keyboards import RerunCallback, keyboard_modes

log = structlog.stdlib.get_logger()

type InFlight = set[tuple[int, int]]  # (chat_id, original message_id)


async def rerun(
    callback: CallbackQuery,
    callback_data: RerunCallback,
    api: ApiClient,
    bot: Bot,
    in_flight: InFlight,
) -> None:
    reply = callback.message
    if not isinstance(reply, Message):
        await callback.answer(TOO_OLD, show_alert=True)
        return
    original = reply.reply_to_message
    if original is None:
        await reply.edit_reply_markup(reply_markup=None)
        await callback.answer(ORIGINAL_GONE, show_alert=True)
        return

    mode = callback_data.mode
    key = (original.chat.id, original.message_id)
    if key in in_flight:
        log.info("rerun_duplicate_ignored", mode=mode.value)
        await callback.answer(ALREADY_WORKING)
        return
    if mode not in keyboard_modes(reply.reply_markup):
        # Stale tap: the keyboard is already gone or changed. Just stop the spinner.
        await callback.answer()
        return
    # Taken before any await: a concurrent tap must see it while we talk to Telegram.
    in_flight.add(key)
    try:
        remaining = [m for m in keyboard_modes(reply.reply_markup) if m != mode]
        await reply.edit_reply_markup(reply_markup=None)
        # Only a toast; Telegram rejects late answers (e.g. taps replayed after a restart).
        with suppress(TelegramBadRequest):
            await callback.answer(rewriting(mode))
        log.info("rerun_requested", mode=mode.value)
        ok = await process_and_reply(
            original, mode, remaining, user_id=callback.from_user.id, api=api, bot=bot
        )
        if not ok:
            await reply.edit_reply_markup(reply_markup=reply.reply_markup)
    finally:
        in_flight.discard(key)


def build_router() -> Router:
    router = Router()
    router.callback_query.register(rerun, RerunCallback.filter())
    return router

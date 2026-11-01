from unittest.mock import AsyncMock

from aiogram.types import Message

from engleks_bot.handlers.start import START_TEXT, start


async def test_start_replies_with_placeholder():
    message = AsyncMock(spec=Message)
    # Message.answer returns an awaitable method object rather than being async def.
    message.answer = AsyncMock()
    await start(message)
    message.answer.assert_awaited_once_with(START_TEXT)

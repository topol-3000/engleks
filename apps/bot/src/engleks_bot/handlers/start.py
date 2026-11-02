from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message
from aiogram.utils.formatting import Text

HELP_TEXT = (
    "Hi! I'm Engleks, your English writing trainer.\n\n"
    "Send me a message in English and I'll correct it, or write in Ukrainian or Russian "
    "and I'll translate it into English. You'll also get short lessons on the patterns "
    "behind the edits."
)


async def start(message: Message) -> None:
    await message.answer(**Text(HELP_TEXT).as_kwargs())


def build_router() -> Router:
    router = Router()
    router.message.register(start, CommandStart())
    router.message.register(start, Command("help"))
    return router

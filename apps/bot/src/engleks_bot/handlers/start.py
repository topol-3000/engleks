from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

HELP_TEXT = (
    "Hi! I'm Engleks, your English writing trainer.\n\n"
    "Send me a message in English and I'll correct it, or write in Ukrainian or Russian "
    "and I'll translate it into English. Every change comes with an explanation.\n\n"
    "Replies start in minimal mode, which fixes only real mistakes. Tap Natural or Polished "
    "under a reply to rewrite the same text in that mode."
)


async def start(message: Message) -> None:
    await message.answer(HELP_TEXT)


def build_router() -> Router:
    router = Router()
    router.message.register(start, CommandStart())
    router.message.register(start, Command("help"))
    return router

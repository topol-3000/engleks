from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.types import Message

START_TEXT = "Hi! I'm Engleks, your English writing trainer. I'm not ready yet, so check back soon."

router = Router()


@router.message(CommandStart())
async def start(message: Message) -> None:
    await message.answer(START_TEXT)

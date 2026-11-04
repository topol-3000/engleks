import structlog
from aiogram import Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import Message, User
from aiogram.utils.formatting import Text
from pydantic import ValidationError

from engleks_bot.api_client import ApiClient, ApiError
from engleks_bot.rendering.errors import INVALID_INVITE, error_text
from engleks_contracts import RedeemInviteRequest, RedeemStatus

log = structlog.stdlib.get_logger()

HELP_TEXT = (
    "Hi! I'm Engleks, your English writing trainer.\n\n"
    "Send me a message in English and I'll correct it, or write in Ukrainian or Russian "
    "and I'll translate it into English. You'll also get short lessons on the patterns "
    "behind the edits."
)
WELCOME = "Your invite worked. Welcome!"


async def start(
    message: Message, command: CommandObject, event_from_user: User, api: ApiClient
) -> None:
    if command.args is None:
        await message.answer(**Text(HELP_TEXT).as_kwargs())
        return
    try:
        request = RedeemInviteRequest(code=command.args)
    except ValidationError:
        # Not logged: the error message would echo the code.
        await message.answer(**Text(INVALID_INVITE).as_kwargs())
        return
    try:
        response = await api.redeem_invite(event_from_user.id, request)
    except ApiError as exc:
        log.warning("api_call_failed", status_code=exc.status_code, code=exc.code)
        await message.answer(**Text(error_text(exc)).as_kwargs())
        return
    if response.status is RedeemStatus.REGISTERED:
        await message.answer(**Text(f"{WELCOME}\n\n{HELP_TEXT}").as_kwargs())
    else:
        await message.answer(**Text(HELP_TEXT).as_kwargs())


async def on_help(message: Message) -> None:
    await message.answer(**Text(HELP_TEXT).as_kwargs())


def build_router() -> Router:
    router = Router()
    router.message.register(start, CommandStart())
    router.message.register(on_help, Command("help"))
    return router

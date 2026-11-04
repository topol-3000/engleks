import hmac
import re
from typing import Annotated

from fastapi import Depends, Header, Request

from engleks_api.application.authenticate import authenticate
from engleks_api.domain.access import AuthContext, Caller, Limits
from engleks_api.domain.ports import IdentityRepository
from engleks_api.domain.text_processing import Provider

# A positive integer in canonical form, so "007" and "7" can never become two identities.
_TELEGRAM_USER_ID = re.compile(r"[1-9][0-9]{0,18}")


class Unauthorized(Exception):
    pass


def identity_repository(request: Request) -> IdentityRepository:
    return request.app.state.identities


def default_limits(request: Request) -> Limits:
    return request.app.state.default_limits


async def bot_caller(
    request: Request,
    x_bot_token: Annotated[str | None, Header()] = None,
    x_telegram_user_id: Annotated[str | None, Header()] = None,
) -> Caller:
    """The Telegram user the bot speaks for, once the bot itself is authenticated."""
    expected = request.app.state.service_token.get_secret_value()
    # Compare bytes: compare_digest raises on non-ASCII str, and == leaks timing.
    if x_bot_token is None or not hmac.compare_digest(x_bot_token.encode(), expected.encode()):
        raise Unauthorized
    if x_telegram_user_id is None or not _TELEGRAM_USER_ID.fullmatch(x_telegram_user_id):
        raise Unauthorized
    return Caller(Provider.TELEGRAM, x_telegram_user_id)


async def auth_context(
    caller: Annotated[Caller, Depends(bot_caller)],
    identities: Annotated[IdentityRepository, Depends(identity_repository)],
    defaults: Annotated[Limits, Depends(default_limits)],
) -> AuthContext:
    return await authenticate(caller.provider, caller.external_id, identities, defaults)

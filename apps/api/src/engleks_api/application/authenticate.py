from engleks_api.domain.access import (
    AuthContext,
    InviteRequired,
    Limits,
    UserDisabled,
    UserStatus,
)
from engleks_api.domain.ports import IdentityRepository
from engleks_api.domain.text_processing import Provider


async def authenticate(
    provider: Provider, external_id: str, identities: IdentityRepository, defaults: Limits
) -> AuthContext:
    user = await identities.find_user(provider, external_id)
    if user is None:
        raise InviteRequired
    if user.status is UserStatus.DISABLED:
        raise UserDisabled
    # `is not None`, not truthiness: an override of 0 is a real limit.
    rpm = user.rate_limit_per_minute
    daily = user.daily_token_budget
    return AuthContext(
        user_id=user.user_id,
        limits=Limits(
            requests_per_minute=defaults.requests_per_minute if rpm is None else rpm,
            daily_tokens=defaults.daily_tokens if daily is None else daily,
        ),
    )

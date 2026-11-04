import structlog

from engleks_api.domain.ports import IdentityRepository, UserAdminRepository
from engleks_api.domain.text_processing import Provider

log = structlog.stdlib.get_logger()


async def set_user_limits(
    external_id: str,
    identities: IdentityRepository,
    admin: UserAdminRepository,
    *,
    per_minute: int | None,
    daily_tokens: int | None,
    reset: bool,
) -> bool:
    """Replace the overrides that are given and keep the others; `reset` clears both."""
    user = await identities.find_user(Provider.TELEGRAM, external_id)
    if user is None:
        return False
    if reset:
        new_per_minute, new_daily = None, None
    else:
        new_per_minute = user.rate_limit_per_minute if per_minute is None else per_minute
        new_daily = user.daily_token_budget if daily_tokens is None else daily_tokens
    changed = await admin.set_limits(Provider.TELEGRAM, external_id, new_per_minute, new_daily)
    if changed:
        log.info(
            "user_limits_set",
            external_id=external_id,
            rate_limit_per_minute=new_per_minute,
            daily_token_budget=new_daily,
        )
    return changed

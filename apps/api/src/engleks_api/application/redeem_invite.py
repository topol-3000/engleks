import structlog

from engleks_api.domain.access import Caller, RedeemResult, UserDisabled, UserStatus
from engleks_api.domain.ports import IdentityRepository, InviteRepository

log = structlog.stdlib.get_logger()


async def redeem_invite(
    caller: Caller, code: str, identities: IdentityRepository, invites: InviteRepository
) -> RedeemResult:
    user = await identities.find_user(caller.provider, caller.external_id)
    if user is not None:
        # The code is not touched: an existing account never spends one.
        if user.status is UserStatus.DISABLED:
            raise UserDisabled
        return RedeemResult.ALREADY_REGISTERED
    redemption = await invites.redeem(code.upper(), caller.provider, caller.external_id)
    if redemption is None:
        return RedeemResult.ALREADY_REGISTERED
    # Ids only: the code itself is never logged.
    log.info(
        "invite_redeemed",
        user_id=str(redemption.user_id),
        invite_code_id=str(redemption.invite_code_id),
    )
    return RedeemResult.REGISTERED

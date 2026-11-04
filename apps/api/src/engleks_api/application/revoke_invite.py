import structlog

from engleks_api.domain.ports import InviteRepository

log = structlog.stdlib.get_logger()


async def revoke_invite(code: str, invites: InviteRepository) -> bool:
    revoked = await invites.revoke(code.upper())
    if revoked:
        log.info("invite_revoked")
    return revoked

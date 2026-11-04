import structlog

from engleks_api.domain.access import Invite, generate_invite_code
from engleks_api.domain.ports import InviteRepository

log = structlog.stdlib.get_logger()


async def create_invite(
    invites: InviteRepository, *, note: str | None, max_uses: int, valid_days: int | None
) -> Invite:
    invite = await invites.create(generate_invite_code(), note, max_uses, valid_days)
    # Ids only: the code itself is never logged.
    log.info("invite_created", invite_code_id=str(invite.id), max_uses=max_uses)
    return invite

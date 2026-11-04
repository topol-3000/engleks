from engleks_api.domain.access import Invite
from engleks_api.domain.ports import InviteRepository


async def list_invites(invites: InviteRepository) -> list[Invite]:
    return await invites.list_all()

from engleks_api.domain.access import UserSummary
from engleks_api.domain.ports import UserAdminRepository


async def list_users(admin: UserAdminRepository) -> list[UserSummary]:
    return await admin.list_with_usage()

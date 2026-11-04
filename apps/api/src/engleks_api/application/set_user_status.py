import structlog

from engleks_api.domain.access import UserStatus
from engleks_api.domain.ports import UserAdminRepository
from engleks_api.domain.text_processing import Provider

log = structlog.stdlib.get_logger()


async def set_user_status(external_id: str, status: UserStatus, admin: UserAdminRepository) -> bool:
    changed = await admin.set_status(Provider.TELEGRAM, external_id, status)
    if changed:
        log.info("user_status_set", external_id=external_id, status=status.value)
    return changed

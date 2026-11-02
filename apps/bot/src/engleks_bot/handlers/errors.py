import structlog
from aiogram.types import ErrorEvent

log = structlog.stdlib.get_logger()


async def on_error(event: ErrorEvent) -> None:
    """Log and swallow any handler failure so one bad update never stops polling."""
    log.error(
        "update_failed",
        update_id=event.update.update_id,
        error_type=type(event.exception).__name__,
    )

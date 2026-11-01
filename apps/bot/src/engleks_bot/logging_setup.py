import logging
import sys
from typing import Literal

import structlog

_handler: logging.Handler | None = None


def configure_logging(log_format: Literal["json", "console"], log_level: str) -> None:
    """Render structlog and stdlib logging (aiogram, libraries) through one handler."""
    global _handler
    level = logging.getLevelNamesMapping()[log_level.upper()]
    shared: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if log_format == "json":
        renderer: list[structlog.typing.Processor] = [
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        renderer = [structlog.dev.ConsoleRenderer()]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[*shared, structlog.stdlib.ExtraAdder()],
            processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, *renderer],
        )
    )
    root = logging.getLogger()
    # Replace only our own previous handler; leave others (e.g. pytest's) alone.
    if _handler is not None:
        root.removeHandler(_handler)
    root.addHandler(handler)
    root.setLevel(level)
    _handler = handler

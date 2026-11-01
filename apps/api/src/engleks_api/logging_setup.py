import logging
from typing import Literal

import structlog


def configure_logging(log_format: Literal["json", "console"], log_level: str) -> None:
    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if log_format == "json":
        processors += [structlog.processors.dict_tracebacks, structlog.processors.JSONRenderer()]
    else:
        processors.append(structlog.dev.ConsoleRenderer())
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[log_level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(),
    )

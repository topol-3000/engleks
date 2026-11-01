import json
import logging
import logging.config

import pytest
import structlog
from uvicorn.config import LOGGING_CONFIG

from engleks_api.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def restore_logging():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.contextvars.clear_contextvars()


def lines(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def test_stdlib_logs_rendered_as_json_with_context(capsys):
    configure_logging("json", "INFO")
    structlog.contextvars.bind_contextvars(request_id="r1")
    logging.getLogger("some.library").info("hello %s", "world")
    [line] = lines(capsys)
    assert (line["event"], line["level"], line["request_id"]) == ("hello world", "info", "r1")


def test_structlog_logs_rendered_as_json(capsys):
    configure_logging("json", "INFO")
    structlog.get_logger().info("evt", k=1)
    [line] = lines(capsys)
    assert (line["event"], line["k"]) == ("evt", 1)


def test_stdlib_level_respected(capsys):
    configure_logging("json", "WARNING")
    logging.getLogger("some.library").info("hidden")
    assert lines(capsys) == []


def test_uvicorn_loggers_routed_through_root(capsys):
    logging.config.dictConfig(LOGGING_CONFIG)  # what uvicorn does before calling the factory
    configure_logging("json", "INFO")
    logging.getLogger("uvicorn.error").info("Started server process")
    [line] = lines(capsys)
    assert line["event"] == "Started server process"


def test_uvicorn_duplicate_app_exception_dropped(capsys):
    configure_logging("json", "INFO")
    logging.getLogger("uvicorn.error").error("Exception in ASGI application")
    assert lines(capsys) == []


def test_sql_statements_not_logged_at_info(capsys):
    configure_logging("json", "INFO")
    logging.getLogger("sqlalchemy.engine.Engine").info("SELECT 1")
    assert lines(capsys) == []


def test_disabled_uvicorn_access_log_stays_disabled(capsys):
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False  # what uvicorn does for --no-access-log
    configure_logging("json", "INFO")
    access.info('127.0.0.1 - "GET /readyz HTTP/1.1" 200')
    assert lines(capsys) == []

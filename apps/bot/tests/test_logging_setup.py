import json
import logging

import pytest
import structlog

from engleks_bot.logging_setup import configure_logging


@pytest.fixture(autouse=True)
def restore_logging():
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)


def test_aiogram_logs_rendered_as_json(capsys):
    configure_logging("json", "INFO")
    logging.getLogger("aiogram.dispatcher").info("Start polling")
    [line] = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x]
    assert (line["event"], line["level"]) == ("Start polling", "info")


def test_structlog_logs_rendered_as_json(capsys):
    configure_logging("json", "INFO")
    structlog.get_logger().info("api_healthy")
    [line] = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x]
    assert line["event"] == "api_healthy"


def test_exception_logged_without_frame_locals(capsys):
    configure_logging("json", "INFO")
    log = structlog.get_logger()

    def handle(code: str) -> None:
        raise ValueError("boom")

    try:
        handle("SECRETINVITECODE")
    except ValueError:
        log.exception("unhandled")
    out = capsys.readouterr().out
    assert "SECRETINVITECODE" not in out
    assert "ValueError" in out

import json
from uuid import UUID

import httpx2
import pytest
import structlog
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage
from aiogram.types import User

from engleks_bot.handlers.start import HELP_TEXT
from engleks_bot.handlers.texts import on_command, on_other, on_text
from engleks_bot.rendering.errors import (
    CANNOT_PROCESS,
    LLM_DOWN,
    MAX_INPUT_CHARS,
    NO_ACCESS,
    SEND_TEXT,
    TOO_LONG,
    UNAVAILABLE,
)
from engleks_bot.rendering.keyboards import keyboard_modes
from engleks_contracts import Category, ChangeItem, Language, Mode, ProcessTextResponse

USER = User(id=1, is_bot=False, first_name="U")


def change(original: str, replacement: str) -> ChangeItem:
    return ChangeItem(
        original=original,
        replacement=replacement,
        category=Category.GRAMMAR,
        explanation=None,
        start=None,
        end=None,
    )


def ok(*, output="I went.", mode=Mode.NATURAL, changes=()) -> ProcessTextResponse:
    return ProcessTextResponse(
        interaction_id=UUID(int=1),
        source_language=Language.EN,
        mode=mode,
        output_text=output,
        changes=list(changes),
    )


def reply_with(response: ProcessTextResponse):
    return lambda _: httpx2.Response(200, json=response.model_dump(mode="json"))


async def test_request_and_reply(make_message, make_api, bot, sent):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx2.Response(
            200, json=ok(changes=[change("has went", "went")]).model_dump(mode="json")
        )

    await on_text(make_message("I has went."), USER, make_api(handler), bot)
    assert json.loads(seen[0].content) == {
        "text": "I has went.",
        "mode": "natural",
        "explanation_language": "en",
    }
    assert seen[0].headers["X-Telegram-User-Id"] == "1"
    [msg] = sent()
    assert msg.reply_parameters.message_id == 10
    assert msg.reply_parameters.allow_sending_without_reply is True
    assert msg.link_preview_options.is_disabled is True and msg.parse_mode is None
    assert keyboard_modes(msg.reply_markup) == [Mode.MINIMAL, Mode.POLISHED]


async def test_markup_in_output_arrives_verbatim(make_message, make_api, bot, sent):
    output = "<b>x</b> *bold* _x_ [a](b)"
    await on_text(make_message("hi"), USER, make_api(reply_with(ok(output=output))), bot)
    [msg] = sent()
    assert msg.text.startswith(output)
    assert msg.parse_mode is None


async def test_long_reply_only_last_has_keyboard(make_message, make_api, bot, sent):
    api = make_api(reply_with(ok(output="word " * 1000)))
    await on_text(make_message("hi"), USER, api, bot)
    msgs = sent()
    assert len(msgs) >= 2
    assert all(m.reply_parameters.message_id == 10 for m in msgs)
    assert all(m.reply_parameters.allow_sending_without_reply is True for m in msgs)
    assert all(m.reply_markup is None for m in msgs[:-1])
    assert keyboard_modes(msgs[-1].reply_markup) == [Mode.MINIMAL, Mode.POLISHED]


@pytest.mark.parametrize(
    ("response", "text"),
    [
        (httpx2.Response(502, json={"code": "llm_unavailable", "message": "x"}), LLM_DOWN),
        (httpx2.Response(401, json={"code": "unauthorized", "message": "x"}), NO_ACCESS),
    ],
)
async def test_api_error_replies_and_logs(response, text, make_message, make_api, bot, sent):
    with structlog.testing.capture_logs() as logs:
        await on_text(make_message(), USER, make_api(lambda _: response), bot)
    [msg] = sent()
    assert msg.text == text
    assert msg.reply_parameters.message_id == 10
    assert msg.reply_markup is None
    [entry] = [e for e in logs if e["event"] == "api_call_failed"]
    assert entry["status_code"] == response.status_code
    assert entry["code"] == response.json()["code"]
    assert entry["mode"] == "natural"


async def test_network_failure(make_message, make_api, bot, sent):
    def handler(_):
        raise httpx2.ConnectError("down")

    await on_text(make_message(), USER, make_api(handler), bot)
    assert [m.text for m in sent()] == [UNAVAILABLE]


async def test_blank_text_makes_no_call_and_no_log(make_message, make_api, bot, sent):
    def handler(_):
        pytest.fail("API must not be called")

    with structlog.testing.capture_logs() as logs:
        await on_text(make_message(" \n "), USER, make_api(handler), bot)
    assert [m.text for m in sent()] == [CANNOT_PROCESS]
    assert logs == []


async def test_text_over_limit_is_rejected_without_api_call(make_message, make_api, bot, sent):
    def handler(_):
        pytest.fail("API must not be called")

    await on_text(make_message("a" * (MAX_INPUT_CHARS + 1)), USER, make_api(handler), bot)
    assert [m.text for m in sent()] == [TOO_LONG]


async def test_text_at_limit_is_processed(make_message, make_api, bot, sent):
    text = "a" * MAX_INPUT_CHARS
    await on_text(make_message(text), USER, make_api(reply_with(ok())), bot)
    assert next(m.text for m in sent()).startswith("I went.")


async def test_logs_never_contain_text(make_message, make_api, bot):
    api = make_api(reply_with(ok(output="Our secret output", changes=[change("a", "b")])))
    with structlog.testing.capture_logs() as logs:
        await on_text(make_message("My secret text"), USER, api, bot)
    [entry] = [e for e in logs if e["event"] == "text_processed"]
    assert entry["interaction_id"] == str(UUID(int=1))
    assert entry["mode"] == "natural"
    assert entry["source_language"] == "en"
    assert entry["text_len"] == 14
    assert entry["changes"] == 1
    assert entry["messages"] == 1
    assert isinstance(entry["duration_ms"], int) and entry["duration_ms"] >= 0
    assert "secret" not in repr(logs)


async def test_text_processed_logged_even_when_send_fails(make_message, make_api, bot, session):
    session.fail[SendMessage] = TelegramBadRequest(
        method=SendMessage(chat_id=1, text="x"), message="Bad Request"
    )
    api = make_api(reply_with(ok()))
    with structlog.testing.capture_logs() as logs, pytest.raises(TelegramBadRequest):
        await on_text(make_message("My text"), USER, api, bot)
    [entry] = [e for e in logs if e["event"] == "text_processed"]
    assert entry["interaction_id"] == str(UUID(int=1))


async def test_command_gets_help(make_message, sent):
    await on_command(make_message("/settings"))
    assert [m.text for m in sent()] == [HELP_TEXT]


async def test_non_text_gets_send_text(make_message, sent):
    photo = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
    await on_other(make_message(None, photo=photo))
    assert [m.text for m in sent()] == [SEND_TEXT]

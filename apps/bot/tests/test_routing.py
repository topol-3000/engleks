import json
from datetime import UTC, datetime
from uuid import UUID

import httpx2
import pytest
import structlog.testing
from aiogram.exceptions import TelegramForbiddenError
from aiogram.methods import SendMessage
from aiogram.types import Update

from engleks_bot.handlers.start import HELP_TEXT, WELCOME
from engleks_bot.main import build_dispatcher
from engleks_bot.rendering.errors import DISABLED, INVALID_INVITE, NEED_INVITE, SEND_TEXT
from engleks_contracts import (
    ErrorCode,
    ErrorResponse,
    Language,
    ProcessTextResponse,
    RedeemInviteResponse,
    RedeemStatus,
)

USER = {"id": 1, "is_bot": False, "first_name": "User"}
BOT_USER = {"id": 42, "is_bot": True, "first_name": "Engleks"}


def now() -> int:
    return int(datetime.now(UTC).timestamp())


def message_dict(text: str | None = "I has went.", *, chat_type="private", message_id=10, **extra):
    data = {
        "message_id": message_id,
        "date": now(),
        "chat": {"id": 1, "type": chat_type},
        "from": USER,
        **extra,
    }
    if text is not None:
        data["text"] = text
    return data


@pytest.fixture
def api_calls():
    return []


@pytest.fixture
def redeem_reply():
    """What /v1/auth/redeem answers: a response DTO, or (status, ErrorResponse)."""
    return {"value": RedeemInviteResponse(status=RedeemStatus.ALREADY_REGISTERED)}


@pytest.fixture
def process_reply():
    """What /v1/texts/process answers instead of a success: (status, ErrorResponse) or None."""
    return {"error": None}


@pytest.fixture
def dispatcher(make_api, api_calls, redeem_reply, process_reply):
    def handler(request):
        if request.url.path == "/v1/auth/redeem":
            api_calls.append(json.loads(request.content))
            reply = redeem_reply["value"]
            if isinstance(reply, RedeemInviteResponse):
                return httpx2.Response(200, json=reply.model_dump(mode="json"))
            status, error = reply
            return httpx2.Response(status, json=error.model_dump(mode="json"))
        if process_reply["error"] is not None:
            status, error = process_reply["error"]
            return httpx2.Response(status, json=error.model_dump(mode="json"))
        api_calls.append(json.loads(request.content))
        body = ProcessTextResponse(
            interaction_id=UUID(int=1),
            source_language=Language.EN,
            output_text="I went.",
            explanations=[],
        )
        return httpx2.Response(200, json=body.model_dump(mode="json"))

    return build_dispatcher(make_api(handler))


@pytest.fixture
def feed(dispatcher, bot):
    async def go(update: dict) -> None:
        await dispatcher.feed_update(bot, Update.model_validate(update, context={"bot": bot}))

    return go


@pytest.mark.parametrize("forwarded", [False, True])
async def test_private_text_processed(forwarded, feed, api_calls, sent):
    extra = {}
    if forwarded:
        extra["forward_origin"] = {"type": "hidden_user", "date": now(), "sender_user_name": "X"}
    await feed({"update_id": 1, "message": message_dict(**extra)})
    assert len(api_calls) == 1
    [msg] = sent()
    assert msg.reply_parameters.message_id == 10


@pytest.mark.parametrize("chat_type", ["group", "supergroup"])
async def test_groups_ignored(chat_type, feed, api_calls, session):
    await feed({"update_id": 1, "message": message_dict(chat_type=chat_type)})
    assert session.requests == []
    assert api_calls == []


@pytest.mark.parametrize("kind", ["channel_post", "edited_message"])
async def test_channel_post_and_edited_message_ignored(kind, feed, api_calls, session):
    chat_type = "channel" if kind == "channel_post" else "private"
    await feed({"update_id": 1, kind: message_dict(chat_type=chat_type)})
    assert session.requests == []
    assert api_calls == []


@pytest.mark.parametrize("text", ["/start", "/help", "/help abc", "/settings"])
async def test_commands_get_help_without_api(text, feed, api_calls, sent):
    await feed({"update_id": 1, "message": message_dict(text)})
    [msg] = sent()
    assert msg.text == HELP_TEXT
    assert api_calls == []


async def test_start_with_code_registers(feed, redeem_reply, api_calls, sent):
    redeem_reply["value"] = RedeemInviteResponse(status=RedeemStatus.REGISTERED)
    await feed({"update_id": 1, "message": message_dict("/start ABC")})
    assert api_calls == [{"code": "ABC"}]
    [msg] = sent()
    assert msg.text == f"{WELCOME}\n\n{HELP_TEXT}"


async def test_start_already_registered(feed, redeem_reply, sent):
    redeem_reply["value"] = RedeemInviteResponse(status=RedeemStatus.ALREADY_REGISTERED)
    await feed({"update_id": 1, "message": message_dict("/start ABC")})
    [msg] = sent()
    assert msg.text == HELP_TEXT


async def test_start_invalid_code(feed, redeem_reply, sent):
    error = ErrorResponse(code=ErrorCode.INVALID_INVITE, message="x")
    redeem_reply["value"] = (400, error)
    await feed({"update_id": 1, "message": message_dict("/start ABC")})
    [msg] = sent()
    assert msg.text == INVALID_INVITE


async def test_start_disabled(feed, redeem_reply, sent):
    error = ErrorResponse(code=ErrorCode.USER_DISABLED, message="x")
    redeem_reply["value"] = (403, error)
    await feed({"update_id": 1, "message": message_dict("/start ABC")})
    [msg] = sent()
    assert msg.text == DISABLED


async def test_start_with_overlong_code(feed, api_calls, sent):
    await feed({"update_id": 1, "message": message_dict("/start " + "A" * 65)})
    [msg] = sent()
    assert msg.text == INVALID_INVITE
    assert api_calls == []


async def test_help_with_args_does_not_redeem(feed, api_calls, sent):
    await feed({"update_id": 1, "message": message_dict("/help abc")})
    [msg] = sent()
    assert msg.text == HELP_TEXT
    assert api_calls == []


async def test_text_from_unregistered_user(feed, process_reply, api_calls, sent):
    error = ErrorResponse(code=ErrorCode.INVITE_REQUIRED, message="x")
    process_reply["error"] = (401, error)
    await feed({"update_id": 1, "message": message_dict("I has went.")})
    [msg] = sent()
    assert msg.text == NEED_INVITE
    assert api_calls == []


async def test_photo_gets_send_text(feed, api_calls, sent):
    photo = [{"file_id": "f", "file_unique_id": "u", "width": 1, "height": 1}]
    await feed(
        {"update_id": 1, "message": message_dict(None, photo=photo, caption="hi")},
    )
    [msg] = sent()
    assert msg.text == SEND_TEXT
    assert api_calls == []


@pytest.mark.parametrize("data", ["rerun:minimal", "other:1"])
async def test_stale_rerun_button_ignored(data, feed, api_calls, session):
    callback = {
        "id": "cb",
        "from": USER,
        "chat_instance": "ci",
        "data": data,
        "message": {**message_dict("I went.", message_id=11), "from": BOT_USER},
    }
    await feed({"update_id": 1, "callback_query": callback})
    assert api_calls == []
    assert session.requests == []


async def test_telegram_error_swallowed_and_logged(feed, session):
    session.fail[SendMessage] = TelegramForbiddenError(
        method=SendMessage(chat_id=1, text="x"),
        message="Forbidden: bot was blocked by the user",
    )
    with structlog.testing.capture_logs() as logs:
        await feed({"update_id": 1, "message": message_dict()})
    [entry] = [e for e in logs if e["event"] == "update_failed"]
    assert entry == {
        "event": "update_failed",
        "log_level": "error",
        "update_id": 1,
        "error_type": "TelegramForbiddenError",
    }

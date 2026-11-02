import asyncio
import json
from uuid import UUID

import httpx2
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import AnswerCallbackQuery, EditMessageReplyMarkup, SendMessage
from aiogram.types import CallbackQuery, Chat, InaccessibleMessage

from engleks_bot.handlers.rerun import rerun
from engleks_bot.rendering.errors import ALREADY_WORKING, LLM_DOWN, ORIGINAL_GONE, TOO_OLD
from engleks_bot.rendering.keyboards import RerunCallback, keyboard_modes, rerun_keyboard
from engleks_contracts import Language, Mode, ProcessTextResponse


def ok(*, output="I went.", mode=Mode.MINIMAL, explanations=()) -> ProcessTextResponse:
    return ProcessTextResponse(
        interaction_id=UUID(int=1),
        source_language=Language.EN,
        mode=mode,
        output_text=output,
        explanations=list(explanations),
    )


def ok_json(mode: Mode) -> httpx2.Response:
    return httpx2.Response(200, json=ok(mode=mode).model_dump(mode="json"))


@pytest.fixture
def make_callback(bot):
    def make(message, mode: Mode) -> CallbackQuery:
        return CallbackQuery.model_validate(
            {
                "id": "cb",
                "from": {"id": 1, "is_bot": False, "first_name": "U"},
                "chat_instance": "ci",
                "message": message,
                "data": RerunCallback(mode=mode).pack(),
            },
            context={"bot": bot},
        )

    return make


@pytest.fixture
def original(make_message):
    return make_message("I has went.", message_id=10)


@pytest.fixture
def reply(make_message, original):
    return make_message(
        "I went. …",
        message_id=11,
        from_bot=True,
        reply_to=original,
        reply_markup=rerun_keyboard([Mode.NATURAL, Mode.POLISHED]),
    )


def of_type(session, method_type):
    return [r for r in session.requests if isinstance(r, method_type)]


async def test_keyboard_removed_before_api_call(make_api, make_callback, bot, session, sent, reply):
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        edits = of_type(session, EditMessageReplyMarkup)
        assert [(e.message_id, e.reply_markup) for e in edits] == [(11, None)]
        [answer] = of_type(session, AnswerCallbackQuery)
        assert answer.text == "Rewriting in natural…"
        return ok_json(Mode.NATURAL)

    in_flight = set()
    cb = make_callback(reply, Mode.NATURAL)
    await rerun(cb, RerunCallback(mode=Mode.NATURAL), make_api(handler), bot, in_flight)

    assert [s["mode"] for s in seen] == ["natural"]
    [msg] = sent()
    assert msg.reply_parameters.message_id == 10
    assert keyboard_modes(msg.reply_markup) == [Mode.POLISHED]
    assert in_flight == set()


async def test_last_mode_leaves_no_keyboard(
    make_api, make_callback, make_message, original, bot, sent
):
    last = make_message(
        "I went. …",
        message_id=11,
        from_bot=True,
        reply_to=original,
        reply_markup=rerun_keyboard([Mode.POLISHED]),
    )
    cb = make_callback(last, Mode.POLISHED)
    api = make_api(lambda _: ok_json(Mode.POLISHED))
    await rerun(cb, RerunCallback(mode=Mode.POLISHED), api, bot, set())
    [msg] = sent()
    assert msg.reply_markup is None


async def test_keyboard_restored_on_api_failure(make_api, make_callback, bot, session, sent, reply):
    body = {"code": "llm_unavailable", "message": "down"}
    api = make_api(lambda _: httpx2.Response(502, json=body))
    in_flight = set()
    cb = make_callback(reply, Mode.NATURAL)
    await rerun(cb, RerunCallback(mode=Mode.NATURAL), api, bot, in_flight)

    edits = of_type(session, EditMessageReplyMarkup)
    assert [e.reply_markup for e in edits] == [None, reply.reply_markup]
    [msg] = sent()
    assert msg.text == LLM_DOWN
    assert msg.reply_parameters.message_id == 10
    assert in_flight == set()


async def test_duplicate_tap_makes_one_call(make_api, make_callback, bot, session, reply):
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def handler(request):
        calls.append(request)
        entered.set()
        await release.wait()
        return ok_json(Mode.NATURAL)

    api = make_api(handler)
    in_flight = set()
    data = RerunCallback(mode=Mode.NATURAL)
    first = asyncio.create_task(
        rerun(make_callback(reply, Mode.NATURAL), data, api, bot, in_flight)
    )
    await entered.wait()
    # wait_for: a regressed guard would block on release.wait() forever instead of failing.
    await asyncio.wait_for(
        rerun(make_callback(reply, Mode.NATURAL), data, api, bot, in_flight), timeout=1
    )
    answers = of_type(session, AnswerCallbackQuery)
    assert [a.text for a in answers][-1] == ALREADY_WORKING
    assert len(calls) == 1
    assert len(of_type(session, EditMessageReplyMarkup)) == 1
    release.set()
    await first
    assert len(calls) == 1
    assert in_flight == set()


async def test_key_held_during_keyboard_edit(make_api, make_callback, bot, session, reply):
    in_flight = set()
    checked = []

    def hook(method):
        if isinstance(method, EditMessageReplyMarkup):
            assert (1, 10) in in_flight
            checked.append(True)

    session.on_request = hook
    api = make_api(lambda _: ok_json(Mode.NATURAL))
    await rerun(
        make_callback(reply, Mode.NATURAL), RerunCallback(mode=Mode.NATURAL), api, bot, in_flight
    )
    assert checked == [True]


async def test_in_flight_released_when_telegram_fails(make_api, make_callback, bot, session, reply):
    session.fail[SendMessage] = TelegramBadRequest(
        method=SendMessage(chat_id=1, text="x"), message="Bad Request"
    )
    in_flight = set()
    api = make_api(lambda _: ok_json(Mode.NATURAL))
    with pytest.raises(TelegramBadRequest):
        await rerun(
            make_callback(reply, Mode.NATURAL),
            RerunCallback(mode=Mode.NATURAL),
            api,
            bot,
            in_flight,
        )
    assert in_flight == set()


async def test_original_gone(make_api, make_callback, make_message, bot, session):
    calls = []

    def handler(request):
        calls.append(request)
        return ok_json(Mode.NATURAL)

    orphan = make_message(
        "I went. …",
        message_id=11,
        from_bot=True,
        reply_markup=rerun_keyboard([Mode.NATURAL]),
    )
    await rerun(
        make_callback(orphan, Mode.NATURAL),
        RerunCallback(mode=Mode.NATURAL),
        make_api(handler),
        bot,
        set(),
    )
    [edit] = of_type(session, EditMessageReplyMarkup)
    assert edit.reply_markup is None
    [answer] = of_type(session, AnswerCallbackQuery)
    assert (answer.text, answer.show_alert) == (ORIGINAL_GONE, True)
    assert calls == []


async def test_inaccessible_message(make_api, make_callback, bot, session):
    calls = []

    def handler(request):
        calls.append(request)
        return ok_json(Mode.NATURAL)

    gone = InaccessibleMessage(chat=Chat(id=1, type="private"), message_id=11, date=0)
    await rerun(
        make_callback(gone, Mode.NATURAL),
        RerunCallback(mode=Mode.NATURAL),
        make_api(handler),
        bot,
        set(),
    )
    [answer] = session.requests
    assert isinstance(answer, AnswerCallbackQuery)
    assert (answer.text, answer.show_alert) == (TOO_OLD, True)
    assert calls == []


async def test_rerun_continues_when_callback_answer_fails(
    make_api, make_callback, bot, session, sent, reply
):
    session.fail[AnswerCallbackQuery] = TelegramBadRequest(
        method=AnswerCallbackQuery(callback_query_id="cb"),
        message="Bad Request: query is too old and response timeout expired",
    )
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return ok_json(Mode.NATURAL)

    in_flight = set()
    await rerun(
        make_callback(reply, Mode.NATURAL),
        RerunCallback(mode=Mode.NATURAL),
        make_api(handler),
        bot,
        in_flight,
    )
    assert [s["mode"] for s in seen] == ["natural"]
    [msg] = sent()
    assert msg.reply_parameters.message_id == 10
    assert keyboard_modes(msg.reply_markup) == [Mode.POLISHED]
    assert in_flight == set()


async def test_stale_tap_on_removed_mode_is_only_acknowledged(
    make_api, make_callback, make_message, original, bot, session
):
    calls = []

    def handler(request):
        calls.append(request)
        return ok_json(Mode.NATURAL)

    stale = make_message(
        "I went. …",
        message_id=11,
        from_bot=True,
        reply_to=original,
        reply_markup=rerun_keyboard([Mode.POLISHED]),
    )
    in_flight = set()
    await rerun(
        make_callback(stale, Mode.NATURAL),
        RerunCallback(mode=Mode.NATURAL),
        make_api(handler),
        bot,
        in_flight,
    )
    [answer] = session.requests
    assert isinstance(answer, AnswerCallbackQuery)
    assert answer.text is None
    assert of_type(session, EditMessageReplyMarkup) == []
    assert calls == []
    assert in_flight == set()

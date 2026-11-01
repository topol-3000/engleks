import itertools
from collections.abc import AsyncGenerator, Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import httpx2
import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import InlineKeyboardMarkup, Message

from engleks_bot.api_client import ApiClient

type Handler = (
    Callable[[httpx2.Request], httpx2.Response]
    | Callable[[httpx2.Request], Coroutine[None, None, httpx2.Response]]
)


class RecordingSession(BaseSession):
    """A Telegram session that records every method and answers with canned results."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod[Any]] = []
        self.fail: dict[type[TelegramMethod[Any]], Exception] = {}
        self.on_request: Callable[[TelegramMethod[Any]], None] | None = None
        self._ids = itertools.count(1000)

    async def close(self) -> None:
        return None

    # `timeout` is the name fixed by aiogram's BaseSession.make_request signature.
    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[Any],
        timeout: int | None = None,  # noqa: ASYNC109
    ) -> Any:
        self.requests.append(method)
        if self.on_request is not None:
            self.on_request(method)
        if (error := self.fail.get(type(method))) is not None:
            raise error
        if isinstance(method, SendMessage):
            return Message.model_validate(
                {
                    "message_id": next(self._ids),
                    "date": datetime.now(UTC),
                    "chat": {"id": method.chat_id, "type": "private"},
                    "text": method.text,
                },
                context={"bot": bot},
            )
        return True

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes]:
        raise NotImplementedError
        yield b""  # pragma: no cover - makes this an async generator


@pytest.fixture
def session() -> RecordingSession:
    return RecordingSession()


@pytest.fixture
def bot(session: RecordingSession) -> Bot:
    return Bot("42:TEST", session=session)


@pytest.fixture
def make_message(bot: Bot) -> Callable[..., Message]:
    def make(
        text: str | None = "I has went.",
        *,
        message_id: int = 10,
        chat_type: str = "private",
        from_bot: bool = False,
        reply_to: Message | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
        **extra: Any,
    ) -> Message:
        data: dict[str, Any] = {
            "message_id": message_id,
            "date": datetime.now(UTC),
            "chat": {"id": 1, "type": chat_type},
            "from_user": (
                {"id": 42, "is_bot": True, "first_name": "Engleks"}
                if from_bot
                else {"id": 1, "is_bot": False, "first_name": "User"}
            ),
            **extra,
        }
        if text is not None:
            data["text"] = text
        if reply_to is not None:
            data["reply_to_message"] = reply_to.model_dump(by_alias=True, exclude_none=True)
        if reply_markup is not None:
            data["reply_markup"] = reply_markup.model_dump(exclude_none=True)
        return Message.model_validate(data, context={"bot": bot})

    return make


@pytest.fixture
async def make_api() -> AsyncGenerator[Callable[[Handler], ApiClient]]:
    clients: list[ApiClient] = []

    def make(handler: Handler) -> ApiClient:
        client = ApiClient("http://api", "tok", transport=httpx2.MockTransport(handler))
        clients.append(client)
        return client

    yield make
    for client in clients:
        await client.aclose()


@pytest.fixture
def sent(session: RecordingSession) -> Callable[[], list[SendMessage]]:
    def get() -> list[SendMessage]:
        return [r for r in session.requests if isinstance(r, SendMessage)]

    return get

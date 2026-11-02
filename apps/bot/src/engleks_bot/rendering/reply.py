from aiogram.utils.formatting import Bold, Text

from engleks_bot.rendering.split import TELEGRAM_LIMIT, split_message, utf16_len
from engleks_contracts import Language, ProcessTextResponse

type Node = str | Bold

_LANGUAGE_NAMES = {Language.UK: "Ukrainian", Language.RU: "Russian"}


def _header(response: ProcessTextResponse) -> Node | None:
    if response.source_language is not Language.EN:
        return Bold(f"Translated from {_LANGUAGE_NAMES[response.source_language]}")
    if response.explanations:
        return Bold("Lessons")
    # An empty list does not mean the text is unchanged: minor fixes go unexplained.
    return None


def _plain(nodes: list[Node]) -> str:
    return Text(*nodes).render()[0]


def render_reply(response: ProcessTextResponse) -> list[Text]:
    """Lay out output, header and lessons, packed into messages within the Telegram limit."""
    header = _header(response)
    blocks: list[tuple[str, list[Node]]] = [
        ("", [response.output_text]),
        *([("\n\n", [header])] if header is not None else []),
        *(("\n", [f"{n}. {e}"]) for n, e in enumerate(response.explanations, 1)),
    ]
    messages: list[Text] = []
    current: list[Node] = []
    current_len = 0

    def flush() -> None:
        nonlocal current, current_len
        if current:
            messages.append(Text(*current))
        current, current_len = [], 0

    for separator, nodes in blocks:
        block_len = utf16_len(_plain(nodes))
        if block_len > TELEGRAM_LIMIT:
            flush()
            messages.extend(Text(chunk) for chunk in split_message(_plain(nodes)))
            continue
        if current and current_len + len(separator) + block_len <= TELEGRAM_LIMIT:
            current = [*current, separator, *nodes]
            current_len += len(separator) + block_len
        else:
            flush()
            current, current_len = list(nodes), block_len
    flush()
    return messages

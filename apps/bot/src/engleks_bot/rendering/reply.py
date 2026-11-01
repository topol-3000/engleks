from aiogram.utils.formatting import Bold, Strikethrough, Text

from engleks_bot.rendering.split import TELEGRAM_LIMIT, split_message, utf16_len
from engleks_contracts import ChangeItem, Language, ProcessTextResponse

type Node = str | Bold | Strikethrough

_LANGUAGE_NAMES = {Language.UK: "Ukrainian", Language.RU: "Russian"}


def _header(response: ProcessTextResponse) -> Node:
    mode = response.mode.value
    if response.source_language is not Language.EN:
        return Bold(f"Translated from {_LANGUAGE_NAMES[response.source_language]} · {mode}")
    if response.changes:
        return Bold(f"Changes · {mode}")
    return Bold(f"No changes needed · {mode}")


def _item(n: int, change: ChangeItem, *, translation: bool) -> list[Node]:
    label = change.category.value.replace("_", " ")
    nodes: list[Node]
    if not change.original:
        nodes = [f"{n}. + {change.replacement} · {label}"]
    else:
        original: Node = change.original if translation else Strikethrough(change.original)
        tail = (
            f" removed · {label}"
            if not change.replacement
            else f" → {change.replacement} · {label}"
        )
        nodes = [f"{n}. ", original, tail]
    if change.explanation is not None:
        nodes.append(f"\n   {change.explanation}")
    return nodes


def _plain(nodes: list[Node]) -> str:
    return Text(*nodes).render()[0]


def render_reply(response: ProcessTextResponse) -> list[Text]:
    """Lay out output, header and change items, packed into messages within the Telegram limit."""
    translation = response.source_language is not Language.EN
    blocks: list[tuple[str, list[Node]]] = [
        ("", [response.output_text]),
        ("\n\n", [_header(response)]),
        *(("\n", _item(n, c, translation=translation)) for n, c in enumerate(response.changes, 1)),
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

TELEGRAM_LIMIT = 4096


def _units(ch: str) -> int:
    """Telegram counts message length in UTF-16 code units."""
    return 2 if ord(ch) > 0xFFFF else 1


def split_message(text: str, limit: int = TELEGRAM_LIMIT) -> list[str]:
    """Split text into Telegram-sized chunks, preferring newline, then space boundaries.

    Whitespace-only chunks are dropped, because Telegram rejects them.
    """
    chunks: list[str] = []
    rest = text
    while rest:
        units = end = 0
        for ch in rest:
            if units + _units(ch) > limit:
                break
            units += _units(ch)
            end += 1
        cut = end
        if end < len(rest):
            prefix = rest[:end]
            newline, space = prefix.rfind("\n"), prefix.rfind(" ")
            if newline >= 0:
                cut = newline + 1
            elif space >= 0:
                cut = space + 1
        chunks.append(rest[:cut])
        rest = rest[cut:]
    return [chunk for chunk in chunks if chunk.strip()]

from collections.abc import Iterable

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from engleks_contracts import Mode


class RerunCallback(CallbackData, prefix="rerun"):
    mode: Mode


def rerun_keyboard(modes: Iterable[Mode]) -> InlineKeyboardMarkup | None:
    wanted = set(modes)
    row = [
        InlineKeyboardButton(
            text=mode.value.capitalize(),
            callback_data=RerunCallback(mode=mode).pack(),
        )
        for mode in Mode
        if mode in wanted
    ]
    if not row:
        return None
    return InlineKeyboardMarkup(inline_keyboard=[row])


def keyboard_modes(markup: InlineKeyboardMarkup | None) -> list[Mode]:
    if markup is None:
        return []
    modes: list[Mode] = []
    for row in markup.inline_keyboard:
        for button in row:
            if button.callback_data is None:
                continue
            try:
                modes.append(RerunCallback.unpack(button.callback_data).mode)
            except ValueError:
                continue
    return modes

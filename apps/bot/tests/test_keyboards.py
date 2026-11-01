from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from engleks_bot.rendering.keyboards import RerunCallback, keyboard_modes, rerun_keyboard
from engleks_contracts import Mode


def labels_and_modes(markup):
    [row] = markup.inline_keyboard
    return [(b.text, RerunCallback.unpack(b.callback_data).mode) for b in row]


def test_buttons_ordered_and_labelled():
    assert labels_and_modes(rerun_keyboard([Mode.POLISHED, Mode.NATURAL])) == [
        ("Natural", Mode.NATURAL),
        ("Polished", Mode.POLISHED),
    ]


def test_empty_gives_none():
    assert rerun_keyboard([]) is None


def test_round_trip():
    assert keyboard_modes(rerun_keyboard([Mode.NATURAL, Mode.POLISHED])) == [
        Mode.NATURAL,
        Mode.POLISHED,
    ]


def test_keyboard_modes_ignores_foreign_buttons():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Site", url="https://example.com"),
                InlineKeyboardButton(text="X", callback_data="other:1"),
                InlineKeyboardButton(text="Y", callback_data="rerun:shouting"),
                InlineKeyboardButton(text="Polished", callback_data="rerun:polished"),
            ]
        ]
    )
    assert keyboard_modes(markup) == [Mode.POLISHED]
    assert keyboard_modes(None) == []

from uuid import UUID

import pytest

from engleks_bot.rendering.reply import render_reply
from engleks_bot.rendering.split import split_message, utf16_len
from engleks_contracts import Category, ChangeItem, Language, Mode, ProcessTextResponse


def change(
    original: str,
    replacement: str,
    category: Category = Category.GRAMMAR,
    explanation: str | None = "Why.",
) -> ChangeItem:
    return ChangeItem(
        original=original,
        replacement=replacement,
        category=category,
        explanation=explanation,
        start=None,
        end=None,
    )


def response(changes, *, output="I went.", mode=Mode.MINIMAL, lang=Language.EN):
    return ProcessTextResponse(
        interaction_id=UUID(int=1),
        source_language=lang,
        mode=mode,
        output_text=output,
        changes=changes,
    )


def kwargs(r):
    return [m.as_kwargs() for m in render_reply(r)]


def ents(kw):
    return [(e.type, e.offset, e.length) for e in kw["entities"]]


def test_edit():
    [kw] = kwargs(response([change("has went", "went", explanation="Past simple.")]))
    assert (
        kw["text"] == "I went.\n\nChanges · minimal\n1. has went → went · grammar\n   Past simple."
    )
    assert ents(kw) == [("bold", 9, 17), ("strikethrough", 30, 8)]
    assert kw["parse_mode"] is None


def test_insertion_deletion_no_explanation_and_label():
    [kw] = kwargs(
        response(
            [
                change("", "the", Category.ARTICLES, None),
                change("very", "", Category.WORD_CHOICE, None),
            ],
            mode=Mode.NATURAL,
        )
    )
    assert kw["text"].endswith(
        "Changes · natural\n1. + the · articles\n2. very removed · word choice"
    )
    assert ents(kw)[-1][0] == "strikethrough"
    assert len([e for e in ents(kw) if e[0] == "strikethrough"]) == 1


@pytest.mark.parametrize("lang,name", [(Language.UK, "Ukrainian"), (Language.RU, "Russian")])
def test_translation(lang, name):
    [kw] = kwargs(response([change("пішов", "went", Category.OTHER, None)], lang=lang))
    assert kw["text"] == f"I went.\n\nTranslated from {name} · minimal\n1. пішов → went · other"
    assert [e[0] for e in ents(kw)] == ["bold"]


def test_translation_header_without_changes():
    [kw] = kwargs(response([], lang=Language.UK))
    assert kw["text"] == "I went.\n\nTranslated from Ukrainian · minimal"


def test_no_changes():
    [kw] = kwargs(response([], mode=Mode.POLISHED))
    assert kw["text"] == "I went.\n\nNo changes needed · polished"


def test_markup_in_output_is_verbatim():
    out = "<b>hi</b> *a* _b_ [x](y)"
    [kw] = kwargs(response([change("*a*", "_b_")], output=out))
    assert kw["text"].startswith(out + "\n\n")
    assert "1. *a* → _b_ · grammar" in kw["text"]
    assert kw["parse_mode"] is None


def test_strikethrough_offset_after_emoji():
    [kw] = kwargs(response([change("ok", "fine", explanation=None)], output="😀"))
    assert ("strikethrough", 25, 2) in ents(kw)


def test_packs_at_block_boundaries():
    texts = [
        kw["text"]
        for kw in kwargs(response([change("a", "b", explanation="x" * 100)], output="a" * 4000))
    ]
    assert texts == ["a" * 4000 + "\n\nChanges · minimal", "1. a → b · grammar\n   " + "x" * 100]


def test_every_message_within_limit_with_astral_chars():
    r = response(
        [change("😀" * 20, "🙂" * 20, explanation="😀" * 50) for _ in range(80)], output="😀" * 1500
    )
    texts = [kw["text"] for kw in kwargs(r)]
    assert len(texts) > 1
    assert all(utf16_len(t) <= 4096 for t in texts)
    assert sum(t.count(" · grammar") for t in texts) == 80


def test_oversized_output_split_into_plain_messages():
    out = "word " * 1000
    msgs = kwargs(response([change("a", "b")], output=out))
    chunks = split_message(out)
    assert [m["text"] for m in msgs[: len(chunks)]] == chunks
    assert all(m["entities"] == [] for m in msgs[: len(chunks)])
    assert msgs[len(chunks)]["text"].startswith("Changes · minimal")

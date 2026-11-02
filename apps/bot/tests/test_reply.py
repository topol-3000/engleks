from uuid import UUID

import pytest

from engleks_bot.rendering.reply import render_reply
from engleks_bot.rendering.split import split_message, utf16_len
from engleks_contracts import Language, Mode, ProcessTextResponse


def response(explanations, *, output="I went.", lang=Language.EN):
    return ProcessTextResponse(
        interaction_id=UUID(int=1),
        source_language=lang,
        mode=Mode.NATURAL,
        output_text=output,
        explanations=explanations,
    )


def kwargs(r):
    return [m.as_kwargs() for m in render_reply(r)]


def ents(kw):
    return [(e.type, e.offset, e.length) for e in kw["entities"]]


def test_lessons():
    [kw] = kwargs(response(["Use the past simple for a finished action.", "Second."]))
    assert kw["text"] == (
        "I went.\n\nLessons\n1. Use the past simple for a finished action.\n2. Second."
    )
    assert ents(kw) == [("bold", 9, 7)]
    assert kw["parse_mode"] is None


@pytest.mark.parametrize("lang,name", [(Language.UK, "Ukrainian"), (Language.RU, "Russian")])
def test_translation(lang, name):
    [kw] = kwargs(response(["A lesson."], lang=lang))
    assert kw["text"] == f"I went.\n\nTranslated from {name}\n1. A lesson."
    assert [e[0] for e in ents(kw)] == ["bold"]


def test_translation_header_without_lessons():
    [kw] = kwargs(response([], lang=Language.UK))
    assert kw["text"] == "I went.\n\nTranslated from Ukrainian"


def test_english_without_lessons_is_output_only():
    # Minor fixes are not explained, so "no changes" would be a false claim.
    [kw] = kwargs(response([]))
    assert kw["text"] == "I went."
    assert kw["entities"] == []


def test_markup_is_verbatim():
    out = "<b>hi</b> *a* _b_ [x](y)"
    [kw] = kwargs(response(["Use *this*."], output=out))
    assert kw["text"] == out + "\n\nLessons\n1. Use *this*."
    assert kw["parse_mode"] is None


def test_header_offset_after_emoji():
    [kw] = kwargs(response(["x"], output="😀"))
    assert ents(kw) == [("bold", 4, 7)]


def test_packs_at_block_boundaries():
    texts = [kw["text"] for kw in kwargs(response(["x" * 100], output="a" * 4000))]
    assert texts == ["a" * 4000 + "\n\nLessons", "1. " + "x" * 100]


def test_every_message_within_limit_with_astral_chars():
    r = response(["🙂" * 50 for _ in range(80)], output="😀" * 1500)
    texts = [kw["text"] for kw in kwargs(r)]
    assert len(texts) > 1
    assert all(utf16_len(t) <= 4096 for t in texts)
    assert sum(t.count("🙂" * 50) for t in texts) == 80


def test_oversized_output_split_into_plain_messages():
    out = "word " * 1000
    msgs = kwargs(response(["A lesson."], output=out))
    chunks = split_message(out)
    assert [m["text"] for m in msgs[: len(chunks)]] == chunks
    assert all(m["entities"] == [] for m in msgs[: len(chunks)])
    assert msgs[len(chunks)]["text"].startswith("Lessons")

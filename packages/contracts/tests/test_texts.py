from uuid import UUID

import pytest
from pydantic import ValidationError

from engleks_contracts import (
    Category,
    ChangeItem,
    ErrorCode,
    Language,
    Mode,
    ProcessTextRequest,
    ProcessTextResponse,
)


def test_request_defaults():
    r = ProcessTextRequest(text="hi")
    assert (r.mode, r.explanation_language) == (Mode.MINIMAL, Language.EN)


@pytest.mark.parametrize("text", ["", " ", "\n\t "])
def test_blank_text_rejected(text):
    with pytest.raises(ValidationError):
        ProcessTextRequest(text=text)


def test_text_is_not_stripped():
    assert ProcessTextRequest(text="  hi \n").text == "  hi \n"


def test_length_limit_is_4096_code_points():
    ProcessTextRequest(text="🙂" * 4096)
    with pytest.raises(ValidationError):
        ProcessTextRequest(text="a" * 4097)


def test_response_round_trip():
    resp = ProcessTextResponse(
        interaction_id=UUID(int=1),
        source_language=Language.EN,
        mode=Mode.NATURAL,
        output_text="Hi, John.",
        changes=[
            ChangeItem(
                original="",
                replacement=",",
                category=Category.PUNCTUATION,
                explanation="Comma after a greeting.",
                start=2,
                end=2,
            ),
            ChangeItem(
                original="john",
                replacement="John",
                category=Category.OTHER,
                explanation=None,
                start=None,
                end=None,
            ),
        ],
    )
    body = resp.model_dump(mode="json")
    assert body["changes"][0] == {
        "original": "",
        "replacement": ",",
        "category": "punctuation",
        "explanation": "Comma after a greeting.",
        "start": 2,
        "end": 2,
    }
    assert ProcessTextResponse.model_validate(body) == resp


def test_new_error_codes():
    assert (ErrorCode.UNAUTHORIZED, ErrorCode.LLM_UNAVAILABLE) == (
        "unauthorized",
        "llm_unavailable",
    )

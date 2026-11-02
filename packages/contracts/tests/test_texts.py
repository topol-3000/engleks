from uuid import UUID

import pytest
from pydantic import ValidationError

import engleks_contracts
from engleks_contracts import (
    ErrorCode,
    Language,
    ProcessTextRequest,
    ProcessTextResponse,
)


def test_request_is_only_text():
    assert ProcessTextRequest(text="hi").model_dump() == {"text": "hi"}


def test_request_ignores_unknown_fields():
    body = {"text": "hi", "mode": "minimal", "explanation_language": "uk"}
    assert ProcessTextRequest.model_validate(body).model_dump() == {"text": "hi"}


def test_mode_is_gone():
    assert "Mode" not in engleks_contracts.__all__


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
        output_text="Hi, John.",
        explanations=["Use a comma after a greeting: 'Hi, John.'"],
    )
    body = resp.model_dump(mode="json")
    assert set(body) == {"interaction_id", "source_language", "output_text", "explanations"}
    assert body["explanations"] == ["Use a comma after a greeting: 'Hi, John.'"]
    assert ProcessTextResponse.model_validate(body) == resp


def test_new_error_codes():
    assert (ErrorCode.UNAUTHORIZED, ErrorCode.LLM_UNAVAILABLE) == (
        "unauthorized",
        "llm_unavailable",
    )

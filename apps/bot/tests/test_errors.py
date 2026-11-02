import pytest

from engleks_bot.api_client import ApiError
from engleks_bot.rendering.errors import (
    CANNOT_PROCESS,
    GENERIC,
    LLM_DOWN,
    NO_ACCESS,
    UNAVAILABLE,
    error_text,
)
from engleks_contracts import ErrorCode


@pytest.mark.parametrize(
    ("error", "text"),
    [
        (ApiError(None, None), UNAVAILABLE),
        (ApiError(401, ErrorCode.UNAUTHORIZED), NO_ACCESS),
        (ApiError(422, ErrorCode.VALIDATION_ERROR), CANNOT_PROCESS),
        (ApiError(502, ErrorCode.LLM_UNAVAILABLE), LLM_DOWN),
        (ApiError(500, ErrorCode.INTERNAL_ERROR), GENERIC),
        (ApiError(502, None), GENERIC),
        (ApiError(429, None), GENERIC),
    ],
)
def test_error_text(error, text):
    assert error_text(error) == text

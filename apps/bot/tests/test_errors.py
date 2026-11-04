import pytest

from engleks_bot.api_client import ApiError
from engleks_bot.rendering.errors import (
    CANNOT_PROCESS,
    DAILY_LIMIT,
    DISABLED,
    GENERIC,
    INVALID_INVITE,
    LLM_DOWN,
    NEED_INVITE,
    NO_ACCESS,
    TOO_MANY,
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
        (ApiError(401, ErrorCode.INVITE_REQUIRED), NEED_INVITE),
        (ApiError(403, ErrorCode.USER_DISABLED), DISABLED),
        (ApiError(429, ErrorCode.RATE_LIMITED), TOO_MANY),
        (ApiError(429, ErrorCode.DAILY_BUDGET), DAILY_LIMIT),
        (ApiError(400, ErrorCode.INVALID_INVITE), INVALID_INVITE),
        (ApiError(500, ErrorCode.INTERNAL_ERROR), GENERIC),
        (ApiError(502, None), GENERIC),
        (ApiError(429, None), GENERIC),
    ],
)
def test_error_text(error, text):
    assert error_text(error) == text

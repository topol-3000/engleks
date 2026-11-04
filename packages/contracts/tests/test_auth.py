import pytest
from pydantic import ValidationError

from engleks_contracts import (
    ErrorCode,
    RedeemInviteRequest,
    RedeemInviteResponse,
    RedeemStatus,
)


def test_access_error_codes():
    assert [
        ErrorCode.INVITE_REQUIRED,
        ErrorCode.USER_DISABLED,
        ErrorCode.INVALID_INVITE,
        ErrorCode.RATE_LIMITED,
        ErrorCode.DAILY_BUDGET,
    ] == ["invite_required", "user_disabled", "invalid_invite", "rate_limited", "daily_budget"]


def test_redeem_request_is_only_code():
    assert RedeemInviteRequest(code="ABC").model_dump() == {"code": "ABC"}


@pytest.mark.parametrize("code", ["", "A" * 65])
def test_redeem_code_length(code):
    with pytest.raises(ValidationError):
        RedeemInviteRequest(code=code)


def test_redeem_code_64_is_ok():
    RedeemInviteRequest(code="A" * 64)


@pytest.mark.parametrize("status", ["registered", "already_registered"])
def test_redeem_response_round_trip(status):
    body = RedeemInviteResponse(status=RedeemStatus(status)).model_dump(mode="json")
    assert body == {"status": status}
    assert RedeemInviteResponse.model_validate(body).status == status

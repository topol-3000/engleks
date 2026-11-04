from uuid import uuid4

import pytest

from engleks_api.application.authenticate import authenticate
from engleks_api.domain.access import (
    AuthContext,
    InviteRequired,
    Limits,
    UserAccess,
    UserDisabled,
    UserStatus,
)
from engleks_api.domain.text_processing import Provider

DEFAULTS = Limits(10, 200000)


def access(
    status: UserStatus = UserStatus.ACTIVE,
    rate_limit_per_minute: int | None = None,
    daily_token_budget: int | None = None,
) -> UserAccess:
    return UserAccess(uuid4(), status, rate_limit_per_minute, daily_token_budget)


class FakeIdentities:
    def __init__(self, users: dict[str, UserAccess] | None = None) -> None:
        self.users = {(Provider.TELEGRAM, ext): u for ext, u in (users or {}).items()}

    async def find_user(self, provider: Provider, external_id: str) -> UserAccess | None:
        return self.users.get((provider, external_id))


async def test_unknown_identity_requires_invite():
    with pytest.raises(InviteRequired):
        await authenticate(Provider.TELEGRAM, "42", FakeIdentities(), DEFAULTS)


async def test_disabled_user_is_rejected():
    ids = FakeIdentities({"42": access(status=UserStatus.DISABLED)})
    with pytest.raises(UserDisabled):
        await authenticate(Provider.TELEGRAM, "42", ids, DEFAULTS)


async def test_active_user_gets_default_limits():
    user = access()
    auth = await authenticate(Provider.TELEGRAM, "42", FakeIdentities({"42": user}), DEFAULTS)
    assert auth == AuthContext(user.user_id, DEFAULTS)


@pytest.mark.parametrize(
    ("rpm", "daily", "expected"),
    [(3, None, Limits(3, 200000)), (None, 500, Limits(10, 500)), (3, 500, Limits(3, 500))],
)
async def test_overrides_apply_over_defaults(rpm, daily, expected):
    ids = FakeIdentities({"42": access(rate_limit_per_minute=rpm, daily_token_budget=daily)})
    assert (await authenticate(Provider.TELEGRAM, "42", ids, DEFAULTS)).limits == expected


async def test_zero_override_is_applied():
    ids = FakeIdentities({"42": access(daily_token_budget=0)})
    assert (await authenticate(Provider.TELEGRAM, "42", ids, DEFAULTS)).limits.daily_tokens == 0

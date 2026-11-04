from uuid import uuid4

from engleks_api.application.set_user_limits import set_user_limits
from engleks_api.domain.access import UserAccess, UserStatus, UserSummary
from engleks_api.domain.text_processing import Provider


class FakeIdentities:
    def __init__(self, user: UserAccess | None) -> None:
        self.user = user

    async def find_user(self, provider: Provider, external_id: str) -> UserAccess | None:
        return self.user


class FakeAdmin:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int | None, int | None]] = []

    async def list_with_usage(self) -> list[UserSummary]:
        raise NotImplementedError

    async def set_status(self, provider: Provider, external_id: str, status: UserStatus) -> bool:
        raise NotImplementedError

    async def set_limits(
        self,
        provider: Provider,
        external_id: str,
        rate_limit_per_minute: int | None,
        daily_token_budget: int | None,
    ) -> bool:
        self.calls.append((external_id, rate_limit_per_minute, daily_token_budget))
        return True


def _user(per_minute: int | None, daily: int | None) -> UserAccess:
    return UserAccess(uuid4(), UserStatus.ACTIVE, per_minute, daily)


async def test_set_limits_keeps_the_other_override():
    admin = FakeAdmin()
    ok = await set_user_limits(
        "42",
        FakeIdentities(_user(None, 500)),
        admin,
        per_minute=5,
        daily_tokens=None,
        reset=False,
    )
    assert ok is True
    assert admin.calls == [("42", 5, 500)]


async def test_reset_clears_both():
    admin = FakeAdmin()
    ok = await set_user_limits(
        "42",
        FakeIdentities(_user(3, 500)),
        admin,
        per_minute=None,
        daily_tokens=None,
        reset=True,
    )
    assert ok is True
    assert admin.calls == [("42", None, None)]


async def test_unknown_user_is_false():
    admin = FakeAdmin()
    ok = await set_user_limits(
        "42",
        FakeIdentities(None),
        admin,
        per_minute=5,
        daily_tokens=None,
        reset=False,
    )
    assert ok is False
    assert admin.calls == []

from uuid import uuid4

import pytest
from structlog.testing import capture_logs

from engleks_api.application.redeem_invite import redeem_invite
from engleks_api.domain.access import (
    Caller,
    InvalidInvite,
    Invite,
    RedeemResult,
    Redemption,
    UserAccess,
    UserDisabled,
    UserStatus,
)
from engleks_api.domain.text_processing import Provider

CODE = "ABCDEFGHJKMN"
CALLER = Caller(Provider.TELEGRAM, "42")


class FakeIdentities:
    def __init__(self, users: dict[str, UserAccess] | None = None) -> None:
        self.users = {(Provider.TELEGRAM, ext): u for ext, u in (users or {}).items()}

    async def find_user(self, provider: Provider, external_id: str) -> UserAccess | None:
        return self.users.get((provider, external_id))


class FakeInvites:
    def __init__(
        self, redemption: Redemption | None = None, fail_with: InvalidInvite | None = None
    ) -> None:
        self.redemption = redemption
        self.fail_with = fail_with
        self.codes: list[str] = []

    async def create(
        self, code: str, note: str | None, max_uses: int, valid_days: int | None
    ) -> Invite:
        raise NotImplementedError

    async def redeem(self, code: str, provider: Provider, external_id: str) -> Redemption | None:
        self.codes.append(code)
        if self.fail_with is not None:
            raise self.fail_with
        return self.redemption


def user(status: UserStatus = UserStatus.ACTIVE) -> UserAccess:
    return UserAccess(uuid4(), status, None, None)


def redemption() -> Redemption:
    return Redemption(uuid4(), uuid4())


async def test_new_user_registered():
    invites = FakeInvites(redemption())
    result = await redeem_invite(CALLER, CODE, FakeIdentities(), invites)
    assert result is RedeemResult.REGISTERED
    assert invites.codes == [CODE]


async def test_lowercase_code_is_accepted():
    invites = FakeInvites(redemption())
    await redeem_invite(CALLER, "abcdefghjkmn", FakeIdentities(), invites)
    assert invites.codes == [CODE]


async def test_active_user_already_registered_code_untouched():
    invites = FakeInvites(redemption())
    result = await redeem_invite(CALLER, CODE, FakeIdentities({"42": user()}), invites)
    assert result is RedeemResult.ALREADY_REGISTERED
    assert invites.codes == []


async def test_disabled_user_raises_code_untouched():
    invites = FakeInvites(redemption())
    ids = FakeIdentities({"42": user(UserStatus.DISABLED)})
    with pytest.raises(UserDisabled):
        await redeem_invite(CALLER, CODE, ids, invites)
    assert invites.codes == []


async def test_concurrent_signup_is_already_registered():
    invites = FakeInvites(None)
    result = await redeem_invite(CALLER, CODE, FakeIdentities(), invites)
    assert result is RedeemResult.ALREADY_REGISTERED


async def test_invalid_invite_propagates():
    invites = FakeInvites(fail_with=InvalidInvite("expired"))
    with pytest.raises(InvalidInvite) as e:
        await redeem_invite(CALLER, CODE, FakeIdentities(), invites)
    assert e.value.reason == "expired"


async def test_logs_ids_not_code():
    done = redemption()
    with capture_logs() as logs:
        await redeem_invite(CALLER, CODE, FakeIdentities(), FakeInvites(done))
    [entry] = [e for e in logs if e["event"] == "invite_redeemed"]
    assert entry["user_id"] == str(done.user_id)
    assert entry["invite_code_id"] == str(done.invite_code_id)
    assert not any(CODE in str(v) for e in logs for v in e.values())

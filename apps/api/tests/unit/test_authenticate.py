from uuid import UUID, uuid4

from engleks_api.application.authenticate import authenticate
from engleks_api.domain.text_processing import Provider


class FakeIdentities:
    def __init__(self) -> None:
        self.users: dict[tuple[Provider, str], UUID] = {}

    async def get_or_create_user(self, provider: Provider, external_id: str) -> UUID:
        return self.users.setdefault((provider, external_id), uuid4())


async def test_same_identity_twice_gives_same_user():
    ids = FakeIdentities()
    a = await authenticate(Provider.TELEGRAM, "42", ids)
    b = await authenticate(Provider.TELEGRAM, "42", ids)
    c = await authenticate(Provider.TELEGRAM, "43", ids)
    assert a == b and a != c

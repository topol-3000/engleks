from engleks_api.domain.ports import IdentityRepository
from engleks_api.domain.text_processing import AuthContext, Provider


async def authenticate(
    provider: Provider, external_id: str, identities: IdentityRepository
) -> AuthContext:
    # Until Access ships, any identity gets an account on first use.
    user_id = await identities.get_or_create_user(provider, external_id)
    return AuthContext(user_id=user_id)

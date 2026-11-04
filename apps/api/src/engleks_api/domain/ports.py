from typing import Protocol
from uuid import UUID

from engleks_api.domain.access import (
    Admission,
    Invite,
    Redemption,
    UserAccess,
    UserStatus,
    UserSummary,
)
from engleks_api.domain.text_processing import (
    Interaction,
    Provider,
    RewriteOutcome,
    Usage,
)


class ReadinessProbe(Protocol):
    """A dependency the service needs before it can take traffic."""

    name: str

    async def check(self) -> None:
        """Return if the dependency is usable; raise otherwise."""
        ...


class TextRewriter(Protocol):
    async def rewrite(self, text: str) -> RewriteOutcome:
        """Improve or translate the text; raise RewriteFailed if no valid result."""
        ...


class IdentityRepository(Protocol):
    async def find_user(self, provider: Provider, external_id: str) -> UserAccess | None:
        """Return the user behind this identity, or None if the identity is unknown."""
        ...


class InviteRepository(Protocol):
    async def create(
        self, code: str, note: str | None, max_uses: int, valid_days: int | None
    ) -> Invite:
        """Store a new invite; it expires `valid_days` from now, or never when None."""
        ...

    async def redeem(self, code: str, provider: Provider, external_id: str) -> Redemption | None:
        """Consume one use of the code and create the user behind this identity.

        Raise InvalidInvite if the code cannot be used. Return None, consuming nothing,
        if the identity already exists.
        """
        ...

    # Named list_all: a method named `list` would shadow the builtin in later annotations.
    async def list_all(self) -> list[Invite]:
        """Every invite, newest first."""
        ...

    async def revoke(self, code: str) -> bool:
        """Revoke the code (keeping the time of an earlier revocation); False if unknown."""
        ...


class UserAdminRepository(Protocol):
    async def list_with_usage(self) -> list[UserSummary]:
        """Every Telegram user with today's requests and tokens (zero when there are none)."""
        ...

    async def set_status(self, provider: Provider, external_id: str, status: UserStatus) -> bool:
        """Set the user's status; False if the identity is unknown."""
        ...

    async def set_limits(
        self,
        provider: Provider,
        external_id: str,
        rate_limit_per_minute: int | None,
        daily_token_budget: int | None,
    ) -> bool:
        """Write both overrides as given (None = use the default); False if unknown."""
        ...


class InteractionRepository(Protocol):
    async def add(self, interaction: Interaction) -> None:
        """Store the interaction."""
        ...


class UsageLedger(Protocol):
    async def admit(self, user_id: UUID) -> Admission:
        """Count this request against the user's rate limit and report today's tokens so far."""
        ...

    async def record(self, user_id: UUID, usage: Usage | None) -> None:
        """Add one LLM call to today's usage; None means the call reported no usage."""
        ...

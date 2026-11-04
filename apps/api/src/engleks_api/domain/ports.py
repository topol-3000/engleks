from typing import Protocol

from engleks_api.domain.access import UserAccess
from engleks_api.domain.text_processing import (
    Interaction,
    Provider,
    RewriteOutcome,
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


class InteractionRepository(Protocol):
    async def add(self, interaction: Interaction) -> None:
        """Store the interaction."""
        ...

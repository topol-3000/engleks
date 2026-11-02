from typing import Protocol
from uuid import UUID

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
    async def get_or_create_user(self, provider: Provider, external_id: str) -> UUID:
        """Return the user behind this identity, creating both if new."""
        ...


class InteractionRepository(Protocol):
    async def add(self, interaction: Interaction) -> None:
        """Store the interaction."""
        ...

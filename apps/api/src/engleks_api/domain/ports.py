from typing import Protocol


class ReadinessProbe(Protocol):
    """A dependency the service needs before it can take traffic."""

    name: str

    async def check(self) -> None:
        """Return if the dependency is usable; raise otherwise."""
        ...

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from engleks_api.domain.text_processing import Provider


class UserStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


@dataclass(frozen=True)
class Caller:
    provider: Provider
    external_id: str


@dataclass(frozen=True)
class Limits:
    requests_per_minute: int
    daily_tokens: int


@dataclass(frozen=True)
class AuthContext:
    user_id: UUID
    limits: Limits


@dataclass(frozen=True)
class UserAccess:
    """What the identity lookup knows about a user; a `None` override means "use the default"."""

    user_id: UUID
    status: UserStatus
    rate_limit_per_minute: int | None
    daily_token_budget: int | None


class InviteRequired(Exception):
    """The identity is unknown: the caller must redeem an invite code first."""


class UserDisabled(Exception):
    pass

import secrets
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Literal
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


@dataclass(frozen=True)
class Admission:
    """What the ledger counted for one request: this request included."""

    requests_this_minute: int
    tokens_today: int


class RateLimited(Exception):
    pass


class BudgetExhausted(Exception):
    pass


class InviteRequired(Exception):
    """The identity is unknown: the caller must redeem an invite code first."""


class UserDisabled(Exception):
    pass


# Uppercase without look-alikes (no I, L, O, 0, 1); fits a Telegram deep link payload.
INVITE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
INVITE_CODE_LENGTH = 12


def generate_invite_code() -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_CODE_LENGTH))


class RedeemResult(StrEnum):
    REGISTERED = "registered"
    ALREADY_REGISTERED = "already_registered"


type InviteRejection = Literal["unknown", "expired", "revoked", "used_up"]


class InvalidInvite(Exception):
    def __init__(self, reason: InviteRejection) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Invite:
    id: UUID
    code: str
    note: str | None
    max_uses: int
    used_count: int
    expires_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class Redemption:
    user_id: UUID
    invite_code_id: UUID

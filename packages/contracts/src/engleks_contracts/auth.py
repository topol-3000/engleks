from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class RedeemStatus(StrEnum):
    REGISTERED = "registered"
    ALREADY_REGISTERED = "already_registered"


class RedeemInviteRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: str = Field(min_length=1, max_length=64)


class RedeemInviteResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: RedeemStatus

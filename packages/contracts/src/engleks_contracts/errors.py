from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    INTERNAL_ERROR = "internal_error"
    VALIDATION_ERROR = "validation_error"
    UNAUTHORIZED = "unauthorized"
    LLM_UNAVAILABLE = "llm_unavailable"
    INVITE_REQUIRED = "invite_required"
    USER_DISABLED = "user_disabled"
    INVALID_INVITE = "invalid_invite"
    RATE_LIMITED = "rate_limited"
    DAILY_BUDGET = "daily_budget"


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str

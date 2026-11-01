from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    INTERNAL_ERROR = "internal_error"
    VALIDATION_ERROR = "validation_error"
    UNAUTHORIZED = "unauthorized"
    LLM_UNAVAILABLE = "llm_unavailable"


class ErrorResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    code: ErrorCode
    message: str

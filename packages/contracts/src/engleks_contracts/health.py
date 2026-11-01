from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class HealthStatus(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: HealthStatus

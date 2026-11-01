from fastapi import APIRouter

from engleks_contracts import HealthResponse, HealthStatus

router = APIRouter()


@router.get("/healthz")
async def healthz() -> HealthResponse:
    return HealthResponse(status=HealthStatus.OK)

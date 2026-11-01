from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response

from engleks_api.application.check_readiness import check_readiness
from engleks_api.domain.ports import ReadinessProbe
from engleks_contracts import HealthResponse, HealthStatus

router = APIRouter()


def readiness_probes(request: Request) -> Sequence[ReadinessProbe]:
    return request.app.state.readiness_probes


@router.get("/healthz")
async def healthz() -> HealthResponse:
    return HealthResponse(status=HealthStatus.OK)


@router.get("/readyz", responses={503: {"model": HealthResponse}})
async def readyz(
    response: Response,
    probes: Annotated[Sequence[ReadinessProbe], Depends(readiness_probes)],
) -> HealthResponse:
    if await check_readiness(probes):
        return HealthResponse(status=HealthStatus.OK)
    response.status_code = 503
    return HealthResponse(status=HealthStatus.UNAVAILABLE)

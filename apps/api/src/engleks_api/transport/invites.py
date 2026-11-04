from typing import Annotated

from fastapi import APIRouter, Depends, Request

from engleks_api.application.redeem_invite import redeem_invite
from engleks_api.domain.access import Caller
from engleks_api.domain.ports import IdentityRepository, InviteRepository
from engleks_api.transport.auth import bot_caller, identity_repository
from engleks_contracts import (
    ErrorResponse,
    RedeemInviteRequest,
    RedeemInviteResponse,
    RedeemStatus,
)

router = APIRouter()


def invite_repository(request: Request) -> InviteRepository:
    return request.app.state.invites


@router.post(
    "/v1/auth/redeem",
    responses={
        400: {"model": ErrorResponse},
        401: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
    },
)
async def redeem(
    body: RedeemInviteRequest,
    caller: Annotated[Caller, Depends(bot_caller)],
    identities: Annotated[IdentityRepository, Depends(identity_repository)],
    invites: Annotated[InviteRepository, Depends(invite_repository)],
) -> RedeemInviteResponse:
    result = await redeem_invite(caller, body.code, identities, invites)
    return RedeemInviteResponse(status=RedeemStatus(result.value))

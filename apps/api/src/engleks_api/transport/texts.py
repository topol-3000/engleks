from typing import Annotated

from fastapi import APIRouter, Depends, Request

from engleks_api.application.process_text import process_text
from engleks_api.domain.ports import InteractionRepository, TextRewriter
from engleks_api.domain.text_processing import AuthContext, Language, Mode, RewriteRequest
from engleks_api.transport.auth import auth_context
from engleks_contracts import ErrorResponse, ProcessTextRequest, ProcessTextResponse
from engleks_contracts import Language as WireLanguage
from engleks_contracts import Mode as WireMode

router = APIRouter()


def text_rewriter(request: Request) -> TextRewriter:
    return request.app.state.rewriter


def interaction_repository(request: Request) -> InteractionRepository:
    return request.app.state.interactions


@router.post(
    "/v1/texts/process",
    responses={
        401: {"model": ErrorResponse},
        422: {"model": ErrorResponse},
        502: {"model": ErrorResponse},
    },
)
async def process(
    body: ProcessTextRequest,
    auth: Annotated[AuthContext, Depends(auth_context)],
    rewriter: Annotated[TextRewriter, Depends(text_rewriter)],
    interactions: Annotated[InteractionRepository, Depends(interaction_repository)],
) -> ProcessTextResponse:
    request = RewriteRequest(
        text=body.text,
        mode=Mode(body.mode.value),
        explanation_language=Language(body.explanation_language.value),
    )
    result = await process_text(auth, request, rewriter, interactions)
    return ProcessTextResponse(
        interaction_id=result.interaction_id,
        source_language=WireLanguage(result.source_language.value),
        mode=WireMode(result.mode.value),
        output_text=result.output_text,
        explanations=result.explanations,
    )

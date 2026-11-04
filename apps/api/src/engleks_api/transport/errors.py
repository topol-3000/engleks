import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from engleks_api.domain.access import (
    BudgetExhausted,
    InvalidInvite,
    InviteRequired,
    RateLimited,
    UserDisabled,
)
from engleks_api.domain.text_processing import RewriteFailed
from engleks_api.transport.auth import Unauthorized
from engleks_contracts import ErrorCode, ErrorResponse

log = structlog.stdlib.get_logger()


def _error(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(code=code, message=message).model_dump(mode="json")
    return JSONResponse(body, status_code=status_code)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, ErrorCode.VALIDATION_ERROR, "Invalid request")

    @app.exception_handler(Unauthorized)
    async def unauthorized(request: Request, exc: Unauthorized) -> JSONResponse:
        return _error(401, ErrorCode.UNAUTHORIZED, "Unauthorized")

    @app.exception_handler(InviteRequired)
    async def invite_required(request: Request, exc: InviteRequired) -> JSONResponse:
        return _error(401, ErrorCode.INVITE_REQUIRED, "Invite required")

    @app.exception_handler(InvalidInvite)
    async def invalid_invite(request: Request, exc: InvalidInvite) -> JSONResponse:
        log.info("invite_rejected", reason=exc.reason)
        return _error(400, ErrorCode.INVALID_INVITE, "Invalid invite")

    @app.exception_handler(UserDisabled)
    async def user_disabled(request: Request, exc: UserDisabled) -> JSONResponse:
        return _error(403, ErrorCode.USER_DISABLED, "User disabled")

    @app.exception_handler(RateLimited)
    async def rate_limited(request: Request, exc: RateLimited) -> JSONResponse:
        return _error(429, ErrorCode.RATE_LIMITED, "Too many requests")

    @app.exception_handler(BudgetExhausted)
    async def budget_exhausted(request: Request, exc: BudgetExhausted) -> JSONResponse:
        return _error(429, ErrorCode.DAILY_BUDGET, "Daily budget exhausted")

    @app.exception_handler(RewriteFailed)
    async def rewrite_failed(request: Request, exc: RewriteFailed) -> JSONResponse:
        log.warning("rewrite_failed", reason=exc.reason)
        return _error(502, ErrorCode.LLM_UNAVAILABLE, "LLM unavailable")

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # Runs in Starlette's outermost middleware, after RequestIdMiddleware has
        # logged the exception, so the header is restored here from request state.
        response = _error(500, ErrorCode.INTERNAL_ERROR, "Internal server error")
        request_id = getattr(request.state, "request_id", None)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

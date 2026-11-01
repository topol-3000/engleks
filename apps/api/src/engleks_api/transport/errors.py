from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from engleks_contracts import ErrorCode, ErrorResponse


def _error(status_code: int, code: ErrorCode, message: str) -> JSONResponse:
    body = ErrorResponse(code=code, message=message).model_dump(mode="json")
    return JSONResponse(body, status_code=status_code)


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(422, ErrorCode.VALIDATION_ERROR, "Invalid request")

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        # Runs in Starlette's outermost middleware, after RequestIdMiddleware has
        # logged the exception, so the header is restored here from request state.
        response = _error(500, ErrorCode.INTERNAL_ERROR, "Internal server error")
        request_id = getattr(request.state, "request_id", None)
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response

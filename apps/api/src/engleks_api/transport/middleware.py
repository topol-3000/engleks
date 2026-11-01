import re
import time
from uuid import uuid4

import structlog
from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_VALID_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")

log = structlog.get_logger()


class RequestIdMiddleware:
    """Assigns a request id, binds it to the log context and echoes it in X-Request-ID."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = Headers(scope=scope).get("x-request-id")
        valid = incoming is not None and _VALID_REQUEST_ID.fullmatch(incoming)
        request_id = incoming if valid and incoming else uuid4().hex
        # Read by the unhandled-error handler, which runs outside this middleware.
        scope.setdefault("state", {})["request_id"] = request_id
        status = 500
        start = time.perf_counter()

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        with structlog.contextvars.bound_contextvars(request_id=request_id):
            try:
                await self.app(scope, receive, send_with_id)
            except Exception:
                log.exception("unhandled_exception")
                raise
            finally:
                log.info(
                    "request",
                    method=scope["method"],
                    path=scope["path"],
                    status=status,
                    duration_ms=round((time.perf_counter() - start) * 1000, 1),
                )

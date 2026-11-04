from types import TracebackType
from typing import Self

import httpx2
from pydantic import BaseModel, ValidationError

from engleks_contracts import (
    ErrorCode,
    ErrorResponse,
    HealthResponse,
    ProcessTextRequest,
    ProcessTextResponse,
    RedeemInviteRequest,
    RedeemInviteResponse,
)


class ApiError(Exception):
    """A failed API call. status_code is None when the API could not be reached;
    code is None when the error body is not an ErrorResponse this bot understands."""

    def __init__(self, status_code: int | None, code: ErrorCode | None) -> None:
        super().__init__(f"API error: status={status_code} code={code}")
        self.status_code = status_code
        self.code = code


class ApiClient:
    def __init__(
        self,
        base_url: str,
        service_token: str,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        timeout: float = 10.0,
        process_timeout: float = 130.0,
    ) -> None:
        self._process_timeout = process_timeout
        self._http = httpx2.AsyncClient(
            base_url=base_url,
            headers={"X-Bot-Token": service_token},
            transport=transport,
            timeout=timeout,
        )

    async def health(self) -> HealthResponse:
        return await self._request("GET", "/healthz", HealthResponse)

    async def process_text(
        self, telegram_user_id: int, request: ProcessTextRequest
    ) -> ProcessTextResponse:
        return await self._request(
            "POST",
            "/v1/texts/process",
            ProcessTextResponse,
            json=request.model_dump(mode="json"),
            headers={"X-Telegram-User-Id": str(telegram_user_id)},
            timeout=self._process_timeout,
        )

    async def redeem_invite(
        self, telegram_user_id: int, request: RedeemInviteRequest
    ) -> RedeemInviteResponse:
        return await self._request(
            "POST",
            "/v1/auth/redeem",
            RedeemInviteResponse,
            json=request.model_dump(mode="json"),
            headers={"X-Telegram-User-Id": str(telegram_user_id)},
        )

    async def _request[T: BaseModel](
        self,
        method: str,
        path: str,
        model: type[T],
        *,
        json: object | None = None,
        headers: dict[str, str] | None = None,
        timeout: float | None = None,  # noqa: ASYNC109 - passed to httpx2, not an asyncio timeout
    ) -> T:
        try:
            response = await self._http.request(
                method,
                path,
                json=json,
                headers=headers,
                timeout=httpx2.USE_CLIENT_DEFAULT if timeout is None else timeout,
            )
        except httpx2.HTTPError as exc:
            raise ApiError(None, None) from exc
        if not response.is_success:
            raise ApiError(response.status_code, _error_code(response))
        try:
            return model.model_validate_json(response.content)
        except ValidationError as exc:
            raise ApiError(response.status_code, None) from exc

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.aclose()


def _error_code(response: httpx2.Response) -> ErrorCode | None:
    try:
        return ErrorResponse.model_validate_json(response.content).code
    except ValidationError:
        return None

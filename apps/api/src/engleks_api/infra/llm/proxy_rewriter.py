import time
from decimal import Decimal, InvalidOperation

import httpx2
import structlog
from jinja2 import Environment, PackageLoader, StrictUndefined
from pydantic import BaseModel, ValidationError

from engleks_api.domain.text_processing import (
    Category,
    Language,
    ModelChange,
    RewriteFailed,
    RewriteOutcome,
    RewriteRequest,
    Usage,
)
from engleks_api.infra.llm.schema import LLMRewrite

log = structlog.stdlib.get_logger()

PROMPT_VERSION = "process_text.v1"
_TEMPERATURE = 0.2
_LANGUAGE_NAMES = {Language.EN: "English", Language.UK: "Ukrainian", Language.RU: "Russian"}

_prompts = Environment(
    loader=PackageLoader("engleks_api.infra.llm", "prompts"),
    undefined=StrictUndefined,
    autoescape=False,
)


class _Message(BaseModel):
    content: str | None = None


class _Choice(BaseModel):
    message: _Message


class _TokenUsage(BaseModel):
    prompt_tokens: int
    completion_tokens: int


class _Completion(BaseModel):
    """The proxy's OpenAI-style envelope; the model's reply is in choices[0]."""

    model: str
    choices: list[_Choice]
    usage: _TokenUsage


class _Attempt(BaseModel):
    content: str
    model: str
    tokens_in: int
    tokens_out: int
    cost: Decimal | None


class ProxyRewriter:
    """TextRewriter that calls only the LiteLLM proxy's OpenAI-compatible API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout_s: float,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
    ) -> None:
        self._model = model
        self._client = httpx2.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def rewrite(self, request: RewriteRequest) -> RewriteOutcome:
        system = _prompts.get_template(f"{PROMPT_VERSION}.j2").render(
            mode=request.mode.value,
            explanation_language=_LANGUAGE_NAMES[request.explanation_language],
        )
        # The user's text is only ever the user message, so it stays data.
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": request.text},
        ]
        first = await self._call(messages, attempt=1)
        attempts = [first]
        try:
            parsed = LLMRewrite.model_validate_json(first.content)
        except ValidationError as exc:
            # A plain resend at low temperature tends to repeat the mistake.
            messages += [
                {"role": "assistant", "content": first.content},
                {
                    "role": "user",
                    "content": "Your reply did not match the required JSON schema: "
                    f"{exc}. Reply again with only the corrected JSON.",
                },
            ]
            second = await self._call(messages, attempt=2)
            attempts.append(second)
            try:
                parsed = LLMRewrite.model_validate_json(second.content)
            except ValidationError:
                raise RewriteFailed("invalid_output") from None

        costs = [a.cost for a in attempts]
        usage = Usage(
            model=attempts[-1].model,
            prompt_version=PROMPT_VERSION,
            tokens_in=sum(a.tokens_in for a in attempts),
            tokens_out=sum(a.tokens_out for a in attempts),
            cost=None if None in costs else sum((c for c in costs if c is not None), Decimal(0)),
        )
        return RewriteOutcome(
            source_language=Language(parsed.source_language),
            output_text=parsed.output_text,
            changes=[
                ModelChange(c.original, c.replacement, Category(c.category), c.explanation)
                for c in parsed.changes
            ],
            usage=usage,
            llm_response=parsed.model_dump(mode="json"),
        )

    async def _call(self, messages: list[dict[str, str]], *, attempt: int) -> _Attempt:
        body = {
            "model": self._model,
            "messages": messages,
            "temperature": _TEMPERATURE,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "rewrite",
                    "strict": True,
                    "schema": LLMRewrite.model_json_schema(),
                },
            },
        }
        started = time.monotonic()
        try:
            response = await self._client.post("chat/completions", json=body)
        except httpx2.TimeoutException:
            raise RewriteFailed("timeout") from None
        except httpx2.HTTPError:
            raise RewriteFailed("connection_error") from None
        duration_ms = round((time.monotonic() - started) * 1000)
        if not response.is_success:
            raise RewriteFailed(f"http_{response.status_code}")
        try:
            completion = _Completion.model_validate_json(response.content)
        except ValidationError:
            raise RewriteFailed("malformed_response") from None
        if not completion.choices:
            raise RewriteFailed("malformed_response")

        cost = _parse_cost(response.headers.get("x-litellm-response-cost"))
        if cost is None:
            log.warning("llm_cost_unknown", model=completion.model, attempt=attempt)
        log.info(
            "llm_call",
            model=completion.model,
            prompt_version=PROMPT_VERSION,
            attempt=attempt,
            tokens_in=completion.usage.prompt_tokens,
            tokens_out=completion.usage.completion_tokens,
            cost=float(cost) if cost is not None else None,
            duration_ms=duration_ms,
        )
        return _Attempt(
            content=completion.choices[0].message.content or "",
            model=completion.model,
            tokens_in=completion.usage.prompt_tokens,
            tokens_out=completion.usage.completion_tokens,
            cost=cost,
        )


def _parse_cost(header: str | None) -> Decimal | None:
    if header is None:
        return None
    try:
        cost = Decimal(header)
    except InvalidOperation:
        return None
    return cost if cost.is_finite() and cost >= 0 else None

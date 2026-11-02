import json
from collections.abc import Callable
from decimal import Decimal

import httpx2
import pytest
import structlog

from engleks_api.domain.text_processing import (
    Language,
    Mode,
    RewriteFailed,
    RewriteRequest,
    Usage,
)
from engleks_api.infra.llm.proxy_rewriter import ProxyRewriter

Handler = Callable[[httpx2.Request], httpx2.Response]

TEXT = "He go home."
REQ = RewriteRequest(TEXT, Mode.MINIMAL, Language.UK)
VALID = json.dumps(
    {
        "source_language": "en",
        "output_text": "He goes home.",
        "explanations": ["Add -s to the verb after he, she or it: 'He goes home.'"],
    }
)


def completion(
    content: str | None,
    *,
    model: str = "ollama_chat/gemma3:4b",
    tokens: tuple[int, int] = (100, 50),
    cost: str | None = "0.0001",
) -> httpx2.Response:
    # Like the real proxy: the body names the alias, a header names the upstream model.
    body = {
        "model": "engleks-rewrite",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]},
    }
    headers = {"x-litellm-model-name": model}
    if cost is not None:
        headers["x-litellm-response-cost"] = cost
    return httpx2.Response(200, json=body, headers=headers)


def make(handler: Handler) -> ProxyRewriter:
    return ProxyRewriter(
        "http://litellm:4000/v1",
        "sk-test",
        "engleks-rewrite",
        5,
        transport=httpx2.MockTransport(handler),
    )


def recording(replies: list[httpx2.Response], seen: list[httpx2.Request]) -> Handler:
    it = iter(replies)

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return next(it)

    return handler


def raises(exc: Exception) -> Handler:
    def handler(_: httpx2.Request) -> httpx2.Response:
        raise exc

    return handler


async def test_valid_reply():
    out = await make(lambda _: completion(VALID)).rewrite(REQ)
    assert (out.source_language, out.output_text) == (Language.EN, "He goes home.")
    assert out.explanations == ["Add -s to the verb after he, she or it: 'He goes home.'"]
    assert out.usage == Usage(
        "ollama_chat/gemma3:4b", "process_text.v3", 100, 50, Decimal("0.0001")
    )
    assert out.llm_response == json.loads(VALID)


async def test_request_shape():
    seen: list[httpx2.Request] = []
    await make(recording([completion(VALID)], seen)).rewrite(REQ)
    body = json.loads(seen[0].content)
    assert seen[0].url.path == "/v1/chat/completions"
    assert seen[0].headers["Authorization"] == "Bearer sk-test"
    assert (body["model"], body["temperature"], body["response_format"]["type"]) == (
        "engleks-rewrite",
        0.2,
        "json_schema",
    )
    assert body["response_format"]["json_schema"]["strict"] is True
    system, user = body["messages"]
    assert user == {"role": "user", "content": TEXT}
    assert system["role"] == "system"
    assert TEXT not in system["content"]
    assert system["content"].startswith("You are an English writing coach.")


async def test_system_prompt_is_the_same_for_every_mode_and_explanation_language():
    seen: list[httpx2.Request] = []
    requests = [RewriteRequest(TEXT, mode, lang) for mode in Mode for lang in Language]
    rewriter = make(recording([completion(VALID) for _ in requests], seen))
    for request in requests:
        await rewriter.rewrite(request)
    systems = {json.loads(r.content)["messages"][0]["content"] for r in seen}
    assert len(systems) == 1
    assert "Write every explanation in English" in systems.pop()


async def test_invalid_then_valid_sends_correction_and_sums_usage():
    seen: list[httpx2.Request] = []
    replies = [completion("not json", cost="0.0001"), completion(VALID, cost="0.0002")]
    out = await make(recording(replies, seen)).rewrite(REQ)
    retry = json.loads(seen[1].content)["messages"]
    assert len(retry) == 4
    assert retry[2] == {"role": "assistant", "content": "not json"}
    assert retry[3]["role"] == "user"
    assert "did not match the required JSON schema" in retry[3]["content"]
    assert (out.usage.tokens_in, out.usage.tokens_out, out.usage.cost) == (
        200,
        100,
        Decimal("0.0003"),
    )


@pytest.mark.parametrize(
    "bad",
    [
        "not json",
        '{"source_language": "de", "output_text": "x", "explanations": []}',
        '{"source_language": "en", "output_text": "  ", "explanations": []}',
        '{"source_language": "en", "output_text": "x", "explanations": [" "]}',
        '{"source_language": "en", "output_text": "x", "explanations": [], "extra": 1}',
        '{"source_language": "en", "output_text": "x", "changes": []}',
        None,
        "",
    ],
)
async def test_invalid_twice_fails(bad):
    with pytest.raises(RewriteFailed) as e:
        await make(lambda _: completion(bad)).rewrite(REQ)
    assert e.value.reason == "invalid_output"


@pytest.mark.parametrize(
    ("handler", "reason"),
    [
        (raises(httpx2.ReadTimeout("t")), "timeout"),
        (raises(httpx2.ConnectError("c")), "connection_error"),
        (lambda _: httpx2.Response(503, text="upstream down"), "http_503"),
        (lambda _: httpx2.Response(401, json={"error": "bad key"}), "http_401"),
        (lambda _: httpx2.Response(200, text="<html>oops</html>"), "malformed_response"),
        (lambda _: httpx2.Response(200, json={"choices": []}), "malformed_response"),
        (lambda _: httpx2.Response(200, json=[1, 2]), "malformed_response"),
    ],
)
async def test_transport_and_envelope_failures(handler, reason):
    with pytest.raises(RewriteFailed) as e:
        await make(handler).rewrite(REQ)
    assert e.value.reason == reason


async def test_model_falls_back_to_body_without_upstream_header():
    response = completion(VALID)
    del response.headers["x-litellm-model-name"]
    out = await make(lambda _: response).rewrite(REQ)
    assert out.usage.model == "engleks-rewrite"


async def test_missing_cost_header_gives_none():
    out = await make(lambda _: completion(VALID, cost=None)).rewrite(REQ)
    assert out.usage.cost is None


async def test_unknown_cost_on_one_attempt_makes_total_unknown():
    replies = [completion("not json", cost=None), completion(VALID, cost="0.0002")]
    out = await make(recording(replies, [])).rewrite(REQ)
    assert out.usage.cost is None


async def test_logs_each_attempt_without_text():
    replies = [completion("not json"), completion(VALID)]
    with structlog.testing.capture_logs() as logs:
        await make(recording(replies, [])).rewrite(REQ)
    calls = [entry for entry in logs if entry["event"] == "llm_call"]
    assert [c["attempt"] for c in calls] == [1, 2]
    assert {"model", "prompt_version", "tokens_in", "tokens_out", "cost", "duration_ms"} <= calls[
        0
    ].keys()
    assert not any(
        TEXT in str(v) or "He goes home" in str(v) for entry in logs for v in entry.values()
    )

from collections.abc import AsyncIterator

import pytest

from engleks_api.config import Settings
from engleks_api.domain.diff import diff, reconcile
from engleks_api.domain.text_processing import Language, Mode, RewriteRequest
from engleks_api.infra.llm.proxy_rewriter import ProxyRewriter

# Through the running LiteLLM proxy (make up; API_LLM_* in the local .env).
pytestmark = pytest.mark.llm


@pytest.fixture
async def rewriter() -> AsyncIterator[ProxyRewriter]:
    settings = Settings()  # pyright: ignore[reportCallIssue]  # values come from .env
    r = ProxyRewriter(
        settings.llm_base_url,
        settings.llm_api_key.get_secret_value(),
        settings.llm_model,
        settings.llm_timeout_s,
    )
    yield r
    await r.aclose()


async def test_english_text_gets_explained_positioned_changes(rewriter):
    text = "Yesterday I go to shop and buy two apple."
    out = await rewriter.rewrite(RewriteRequest(text, Mode.MINIMAL, Language.EN))
    assert out.source_language is Language.EN and out.output_text != text
    changes = reconcile(diff(text, out.output_text), out.changes).changes
    assert any(c.explanation and c.span for c in changes)


async def test_ukrainian_text_is_translated(rewriter):
    out = await rewriter.rewrite(
        RewriteRequest("Я вчора ходив до магазину.", Mode.MINIMAL, Language.EN)
    )
    assert out.source_language is Language.UK


async def test_stored_model_is_upstream_not_alias(rewriter):
    out = await rewriter.rewrite(RewriteRequest("Its fine.", Mode.MINIMAL, Language.EN))
    assert out.usage.model != "engleks-rewrite" and out.usage.tokens_in > 0

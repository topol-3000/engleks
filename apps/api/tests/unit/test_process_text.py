from decimal import Decimal
from uuid import uuid4

import pytest

from engleks_api.application.process_text import process_text
from engleks_api.domain.access import AuthContext, Limits
from engleks_api.domain.text_processing import (
    Interaction,
    Language,
    RewriteFailed,
    RewriteOutcome,
    Usage,
)

AUTH = AuthContext(user_id=uuid4(), limits=Limits(10, 200000))


def outcome(lang: Language, output_text: str, explanations: list[str]) -> RewriteOutcome:
    usage = Usage("m", "process_text.v5", 10, 5, Decimal("0.001"))
    return RewriteOutcome(lang, output_text, explanations, usage, llm_response={})


class FakeRewriter:
    def __init__(self, result: RewriteOutcome | RewriteFailed) -> None:
        self.result = result
        self.texts: list[str] = []

    async def rewrite(self, text: str) -> RewriteOutcome:
        self.texts.append(text)
        if isinstance(self.result, RewriteFailed):
            raise self.result
        return self.result


class FakeInteractions:
    def __init__(self) -> None:
        self.saved: list[Interaction] = []

    async def add(self, interaction: Interaction) -> None:
        self.saved.append(interaction)


async def test_returns_and_saves_the_outcome():
    out = outcome(Language.EN, "He goes home.", ["Add -s to the verb after he, she or it."])
    rewriter = FakeRewriter(out)
    repo = FakeInteractions()
    result = await process_text(AUTH, "He go home.", rewriter, repo)
    assert (result.source_language, result.output_text, result.explanations) == (
        Language.EN,
        "He goes home.",
        ["Add -s to the verb after he, she or it."],
    )
    assert repo.saved == [Interaction(result.interaction_id, AUTH.user_id, "He go home.", out)]
    assert result.interaction_id.version == 7
    assert rewriter.texts == ["He go home."]


async def test_translation_without_lessons():
    rewriter = FakeRewriter(outcome(Language.UK, "Hi, how are you?", []))
    result = await process_text(AUTH, "Привіт, як справи?", rewriter, FakeInteractions())
    assert (result.source_language, result.explanations) == (Language.UK, [])


async def test_failure_saves_nothing():
    repo = FakeInteractions()
    with pytest.raises(RewriteFailed):
        await process_text(AUTH, "x", FakeRewriter(RewriteFailed("timeout")), repo)
    assert repo.saved == []

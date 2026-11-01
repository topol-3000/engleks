from decimal import Decimal
from uuid import uuid4

import pytest

from engleks_api.application.process_text import process_text
from engleks_api.domain.text_processing import (
    AuthContext,
    Category,
    Change,
    Interaction,
    Language,
    Mode,
    ModelChange,
    RewriteFailed,
    RewriteOutcome,
    RewriteRequest,
    Usage,
)

AUTH = AuthContext(user_id=uuid4())


def outcome(lang: Language, output_text: str, changes: list[ModelChange]) -> RewriteOutcome:
    usage = Usage("m", "process_text.v1", 10, 5, Decimal("0.001"))
    return RewriteOutcome(lang, output_text, changes, usage, llm_response={})


class FakeRewriter:
    def __init__(self, result: RewriteOutcome | RewriteFailed) -> None:
        self.result = result
        self.requests: list[RewriteRequest] = []

    async def rewrite(self, request: RewriteRequest) -> RewriteOutcome:
        self.requests.append(request)
        if isinstance(self.result, RewriteFailed):
            raise self.result
        return self.result


class FakeInteractions:
    def __init__(self) -> None:
        self.saved: list[Interaction] = []

    async def add(self, interaction: Interaction) -> None:
        self.saved.append(interaction)


async def test_english_path_saves_positioned_changes():
    rewriter = FakeRewriter(
        outcome(
            Language.EN,
            "He goes home.",
            [ModelChange("go", "goes", Category.GRAMMAR, "Third person.")],
        )
    )
    repo = FakeInteractions()
    req = RewriteRequest("He go home.", Mode.MINIMAL, Language.EN)
    result = await process_text(AUTH, req, rewriter, repo)
    assert result.changes == [Change("go", "goes", Category.GRAMMAR, "Third person.", (3, 5))]
    [saved] = repo.saved
    assert (saved.id, saved.user_id, saved.changes, saved.request) == (
        result.interaction_id,
        AUTH.user_id,
        result.changes,
        req,
    )
    assert result.interaction_id.version == 7
    assert rewriter.requests == [req]


async def test_translation_path_skips_diff():
    changes = [ModelChange("як справи", "how are you", Category.WORD_CHOICE, "Set phrase.")]
    rewriter = FakeRewriter(outcome(Language.UK, "Hi, how are you?", changes))
    result = await process_text(
        AUTH,
        RewriteRequest("Привіт, як справи?", Mode.MINIMAL, Language.UK),
        rewriter,
        FakeInteractions(),
    )
    assert result.source_language is Language.UK
    assert result.changes == [
        Change("як справи", "how are you", Category.WORD_CHOICE, "Set phrase.", None)
    ]


async def test_failure_saves_nothing():
    repo = FakeInteractions()
    with pytest.raises(RewriteFailed):
        await process_text(
            AUTH,
            RewriteRequest("x", Mode.MINIMAL, Language.EN),
            FakeRewriter(RewriteFailed("timeout")),
            repo,
        )
    assert repo.saved == []

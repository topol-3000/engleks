from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from structlog.testing import capture_logs

from engleks_api.application.process_text import process_text
from engleks_api.domain.access import (
    Admission,
    AuthContext,
    BudgetExhausted,
    Limits,
    RateLimited,
)
from engleks_api.domain.text_processing import (
    Interaction,
    Language,
    RewriteFailed,
    RewriteOutcome,
    Usage,
)

AUTH = AuthContext(user_id=uuid4(), limits=Limits(requests_per_minute=3, daily_tokens=100))


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
    def __init__(self, events: list[object] | None = None) -> None:
        self.saved: list[Interaction] = []
        self.events = events if events is not None else []

    async def add(self, interaction: Interaction) -> None:
        self.events.append("add")
        self.saved.append(interaction)


class FakeLedger:
    def __init__(self, admission: Admission, events: list[object] | None = None) -> None:
        self.admission = admission
        self.events = events if events is not None else []
        self.admitted: list[UUID] = []

    async def admit(self, user_id: UUID) -> Admission:
        self.admitted.append(user_id)
        return self.admission

    async def record(self, user_id: UUID, usage: Usage | None) -> None:
        self.events.append(("record", usage))


def ledger(requests: int = 1, tokens: int = 0) -> FakeLedger:
    return FakeLedger(Admission(requests, tokens))


async def test_returns_and_saves_the_outcome():
    out = outcome(Language.EN, "He goes home.", ["Add -s to the verb after he, she or it."])
    rewriter = FakeRewriter(out)
    repo = FakeInteractions()
    result = await process_text(AUTH, "He go home.", rewriter, repo, ledger())
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
    result = await process_text(AUTH, "Привіт, як справи?", rewriter, FakeInteractions(), ledger())
    assert (result.source_language, result.explanations) == (Language.UK, [])


async def test_failure_saves_nothing():
    repo = FakeInteractions()
    with pytest.raises(RewriteFailed):
        await process_text(AUTH, "x", FakeRewriter(RewriteFailed("timeout")), repo, ledger())
    assert repo.saved == []


def run(admission: Admission, result: RewriteOutcome | RewriteFailed | None = None):
    events: list[object] = []
    rewriter = FakeRewriter(result or outcome(Language.EN, "ok", []))
    led = FakeLedger(admission, events)
    return rewriter, events, process_text(AUTH, "x", rewriter, FakeInteractions(events), led)


async def test_last_request_in_window_passes():
    _, _, call = run(Admission(3, 0))
    assert (await call).output_text == "ok"


async def test_request_over_rate_limit_rejected():
    rewriter, events, call = run(Admission(4, 0))
    with pytest.raises(RateLimited):
        await call
    assert rewriter.texts == []
    assert events == []


async def test_budget_minus_one_passes():
    _, _, call = run(Admission(1, 99))
    assert (await call).output_text == "ok"


async def test_budget_reached_rejected():
    rewriter, events, call = run(Admission(1, 100))
    with pytest.raises(BudgetExhausted):
        await call
    assert rewriter.texts == []
    assert events == []


async def test_rate_limit_is_checked_before_budget():
    _, _, call = run(Admission(4, 100))
    with pytest.raises(RateLimited):
        await call


async def test_success_records_then_adds():
    out = outcome(Language.EN, "ok", [])
    _, events, call = run(Admission(1, 0), out)
    await call
    assert events == [("record", out.usage), "add"]


async def test_failure_records_partial_usage_and_reraises():
    usage = Usage("m", "process_text.v5", 7, 3, None)
    _, events, call = run(Admission(1, 0), RewriteFailed("invalid_output", usage=usage))
    with pytest.raises(RewriteFailed):
        await call
    assert events == [("record", usage)]


async def test_failure_without_usage_still_records():
    _, events, call = run(Admission(1, 0), RewriteFailed("timeout"))
    with pytest.raises(RewriteFailed):
        await call
    assert events == [("record", None)]


async def test_rejections_logged():
    _, _, limited = run(Admission(4, 0))
    _, _, exhausted = run(Admission(1, 100))
    with capture_logs() as logs, pytest.raises(RateLimited):
        await limited
    with capture_logs() as more, pytest.raises(BudgetExhausted):
        await exhausted
    assert {
        "event": "rate_limited",
        "log_level": "info",
        "user_id": AUTH.user_id,
        "count": 4,
        "limit": 3,
    } in logs
    assert {
        "event": "daily_budget_exhausted",
        "log_level": "info",
        "user_id": AUTH.user_id,
        "tokens_today": 100,
        "limit": 100,
    } in more

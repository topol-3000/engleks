import uuid
from decimal import Decimal

from engleks_api.domain.access import UserAccess, UserStatus
from engleks_api.domain.text_processing import (
    Interaction,
    Language,
    Provider,
    RewriteOutcome,
    Usage,
)
from engleks_api.infra.db.models import InteractionRow
from engleks_api.infra.db.repositories import SqlIdentityRepository, SqlInteractionRepository


def interaction(user_id: uuid.UUID) -> Interaction:
    outcome = RewriteOutcome(
        source_language=Language.EN,
        output_text="He goes home, right?",
        explanations=["Add -s to the verb after he, she or it."],
        usage=Usage("ollama/qwen", "process_text.v5", 120, 45, Decimal("0.00012345")),
        llm_response={
            "source_language": "en",
            "output_text": "He goes home, right?",
            "explanations": ["Add -s to the verb after he, she or it."],
        },
    )
    return Interaction(uuid.uuid7(), user_id, "He go home, no?", outcome)


async def test_find_user_unknown_is_none(sessions):
    assert await SqlIdentityRepository(sessions).find_user(Provider.TELEGRAM, "999001") is None


async def test_find_user_returns_status_and_overrides(sessions, register):
    user_id, ext = await register(sessions, status="disabled", rate_limit_per_minute=3)
    found = await SqlIdentityRepository(sessions).find_user(Provider.TELEGRAM, ext)
    assert found == UserAccess(user_id, UserStatus.DISABLED, 3, None)


async def test_interaction_round_trip(sessions, register):
    user_id, _ = await register(sessions)
    it = interaction(user_id)
    await SqlInteractionRepository(sessions).add(it)
    async with sessions() as s:
        row = await s.get(InteractionRow, it.id)
    assert row is not None
    assert (row.user_id, row.cost, row.prompt_version, row.model) == (
        user_id,
        Decimal("0.00012345"),
        "process_text.v5",
        "ollama/qwen",
    )
    assert row.source_language == "en"
    assert (row.input_text, row.output_text, row.tokens_in, row.tokens_out) == (
        "He go home, no?",
        "He goes home, right?",
        120,
        45,
    )
    assert row.llm_response == it.outcome.llm_response

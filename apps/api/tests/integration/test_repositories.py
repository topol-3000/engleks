import asyncio
import uuid
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from engleks_api.domain.text_processing import (
    Interaction,
    Language,
    Mode,
    Provider,
    RewriteOutcome,
    RewriteRequest,
    Usage,
)
from engleks_api.infra.db.models import CorrectionRow, InteractionRow
from engleks_api.infra.db.repositories import SqlIdentityRepository, SqlInteractionRepository


def interaction(user_id: uuid.UUID) -> Interaction:
    request = RewriteRequest("He go home, no?", Mode.NATURAL, Language.UK)
    outcome = RewriteOutcome(
        source_language=Language.EN,
        output_text="He goes home, right?",
        explanations=["Add -s to the verb after he, she or it."],
        usage=Usage("ollama/qwen", "process_text.v3", 120, 45, Decimal("0.00012345")),
        llm_response={
            "source_language": "en",
            "output_text": "He goes home, right?",
            "explanations": ["Add -s to the verb after he, she or it."],
        },
    )
    return Interaction(uuid.uuid7(), user_id, request, outcome)


async def test_get_or_create_is_stable(sessions):
    repo = SqlIdentityRepository(sessions)
    first = await repo.get_or_create_user(Provider.TELEGRAM, "1001")
    assert await repo.get_or_create_user(Provider.TELEGRAM, "1001") == first
    assert await repo.get_or_create_user(Provider.TELEGRAM, "1002") != first


async def test_concurrent_get_or_create_creates_one_user(engine):
    # Real connections: one connection cannot run statements concurrently.
    repo = SqlIdentityRepository(async_sessionmaker(engine, expire_on_commit=False))
    external_id = str(uuid.uuid4().int % 10**12)
    async with engine.connect() as c:
        before = await c.scalar(text("SELECT count(*) FROM app_user"))
    ids = await asyncio.gather(
        *(repo.get_or_create_user(Provider.TELEGRAM, external_id) for _ in range(5))
    )
    async with engine.connect() as c:
        after = await c.scalar(text("SELECT count(*) FROM app_user"))
    assert len(set(ids)) == 1
    assert after - before == 1


async def test_interaction_round_trip(sessions):
    user_id = await SqlIdentityRepository(sessions).get_or_create_user(Provider.TELEGRAM, "2001")
    it = interaction(user_id)
    await SqlInteractionRepository(sessions).add(it)
    async with sessions() as s:
        row = await s.get(InteractionRow, it.id)
        corrections = await s.scalar(
            select(func.count())
            .select_from(CorrectionRow)
            .where(CorrectionRow.interaction_id == it.id)
        )
    assert row is not None
    assert (row.user_id, row.cost, row.prompt_version, row.model) == (
        user_id,
        Decimal("0.00012345"),
        "process_text.v3",
        "ollama/qwen",
    )
    assert (row.mode, row.source_language, row.explanation_language) == ("natural", "en", "uk")
    assert (row.input_text, row.output_text, row.tokens_in, row.tokens_out) == (
        "He go home, no?",
        "He goes home, right?",
        120,
        45,
    )
    assert row.llm_response == it.outcome.llm_response
    # Lessons live in llm_response; the correction table is no longer written.
    assert corrections == 0

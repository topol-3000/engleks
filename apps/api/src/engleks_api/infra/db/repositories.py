import uuid

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engleks_api.domain.text_processing import Interaction, Provider
from engleks_api.infra.db.models import IdentityRow, InteractionRow, UserRow

# Each method opens its own session and is one transaction, so no connection is held
# between calls (in particular, not during the LLM call).


class SqlIdentityRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def get_or_create_user(self, provider: Provider, external_id: str) -> uuid.UUID:
        select_user = select(IdentityRow.user_id).where(
            IdentityRow.provider == provider.value, IdentityRow.external_id == external_id
        )
        async with self._sessions() as session:
            if (found := await session.scalar(select_user)) is not None:
                return found
            # A user's first two messages can arrive at once: insert-or-nothing, and
            # roll back our user row if another request created the identity first.
            user_id = uuid.uuid7()
            await session.execute(insert(UserRow).values(id=user_id))
            inserted = await session.scalar(
                pg_insert(IdentityRow)
                .values(
                    id=uuid.uuid7(),
                    user_id=user_id,
                    provider=provider.value,
                    external_id=external_id,
                )
                .on_conflict_do_nothing(index_elements=["provider", "external_id"])
                .returning(IdentityRow.user_id)
            )
            if inserted is not None:
                await session.commit()
                return user_id
            await session.rollback()
            winner = await session.scalar(select_user)
            if winner is None:
                raise RuntimeError("identity conflict but no identity row found")
            return winner


class SqlInteractionRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def add(self, interaction: Interaction) -> None:
        outcome, usage = interaction.outcome, interaction.outcome.usage
        async with self._sessions.begin() as session:
            session.add(
                InteractionRow(
                    id=interaction.id,
                    user_id=interaction.user_id,
                    source_language=outcome.source_language.value,
                    input_text=interaction.input_text,
                    output_text=outcome.output_text,
                    llm_response=dict(outcome.llm_response),
                    model=usage.model,
                    prompt_version=usage.prompt_version,
                    tokens_in=usage.tokens_in,
                    tokens_out=usage.tokens_out,
                    cost=usage.cost,
                )
            )

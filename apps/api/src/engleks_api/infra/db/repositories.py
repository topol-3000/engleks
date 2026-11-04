from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engleks_api.domain.access import UserAccess, UserStatus
from engleks_api.domain.text_processing import Interaction, Provider
from engleks_api.infra.db.models import IdentityRow, InteractionRow, UserRow

# Each method opens its own session and is one transaction, so no connection is held
# between calls (in particular, not during the LLM call).


class SqlIdentityRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def find_user(self, provider: Provider, external_id: str) -> UserAccess | None:
        query = (
            select(
                UserRow.id,
                UserRow.status,
                UserRow.rate_limit_per_minute,
                UserRow.daily_token_budget,
            )
            .join(IdentityRow, IdentityRow.user_id == UserRow.id)
            .where(IdentityRow.provider == provider.value, IdentityRow.external_id == external_id)
        )
        async with self._sessions() as session:
            row = (await session.execute(query)).one_or_none()
        if row is None:
            return None
        return UserAccess(
            row.id, UserStatus(row.status), row.rate_limit_per_minute, row.daily_token_budget
        )


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

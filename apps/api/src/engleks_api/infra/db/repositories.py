from uuid import uuid7

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engleks_api.domain.access import (
    InvalidInvite,
    Invite,
    InviteRejection,
    Redemption,
    UserAccess,
    UserStatus,
)
from engleks_api.domain.text_processing import Interaction, Provider
from engleks_api.infra.db.models import IdentityRow, InteractionRow, InviteCodeRow, UserRow

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


class SqlInviteRepository:
    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def create(
        self, code: str, note: str | None, max_uses: int, valid_days: int | None
    ) -> Invite:
        # The database's clock sets the expiry, as it does when the code is checked.
        expires_at = (
            None if valid_days is None else func.now() + func.make_interval(0, 0, 0, valid_days)
        )
        row = InviteCodeRow(
            id=uuid7(), code=code, note=note, max_uses=max_uses, used_count=0, expires_at=expires_at
        )
        async with self._sessions.begin() as session:
            session.add(row)
            await session.flush()
            await session.refresh(row)
            return _invite(row)

    async def redeem(self, code: str, provider: Provider, external_id: str) -> Redemption | None:
        async with self._sessions() as session:
            claim = (
                update(InviteCodeRow)
                .where(
                    InviteCodeRow.code == code,
                    InviteCodeRow.revoked_at.is_(None),
                    (InviteCodeRow.expires_at.is_(None)) | (InviteCodeRow.expires_at > func.now()),
                    InviteCodeRow.used_count < InviteCodeRow.max_uses,
                )
                .values(used_count=InviteCodeRow.used_count + 1)
                .returning(InviteCodeRow.id)
            )
            invite_id = await session.scalar(claim)
            if invite_id is None:
                reason = await _rejection(session, code)
                await session.rollback()
                raise InvalidInvite(reason)
            user = UserRow(id=uuid7(), invite_code_id=invite_id)
            session.add(user)
            await session.flush()
            inserted = await session.scalar(
                pg_insert(IdentityRow)
                .values(
                    id=uuid7(), user_id=user.id, provider=provider.value, external_id=external_id
                )
                .on_conflict_do_nothing(index_elements=["provider", "external_id"])
                .returning(IdentityRow.id)
            )
            if inserted is None:
                # A concurrent redeem by the same user won: give the use back.
                await session.rollback()
                return None
            await session.commit()
            return Redemption(user.id, invite_id)


async def _rejection(session: AsyncSession, code: str) -> InviteRejection:
    row = (
        await session.execute(
            select(
                InviteCodeRow.revoked_at,
                (InviteCodeRow.expires_at <= func.now()).label("is_expired"),
            ).where(InviteCodeRow.code == code)
        )
    ).one_or_none()
    if row is None:
        return "unknown"
    if row.revoked_at is not None:
        return "revoked"
    if row.is_expired:
        return "expired"
    return "used_up"


def _invite(row: InviteCodeRow) -> Invite:
    return Invite(
        row.id,
        row.code,
        row.note,
        row.max_uses,
        row.used_count,
        row.expires_at,
        row.revoked_at,
        row.created_at,
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

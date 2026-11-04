from decimal import Decimal
from uuid import UUID, uuid7

from sqlalchemy import Date, case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from engleks_api.domain.access import (
    Admission,
    InvalidInvite,
    Invite,
    InviteRejection,
    Redemption,
    UserAccess,
    UserStatus,
)
from engleks_api.domain.text_processing import Interaction, Provider, Usage
from engleks_api.infra.db.models import (
    IdentityRow,
    InteractionRow,
    InviteCodeRow,
    RateLimitRow,
    UsageDailyRow,
    UserRow,
)

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


_TODAY_UTC = func.cast(func.timezone("UTC", func.now()), Date)


class SqlUsageLedger:
    """Counts with upserts on the database's clock; the use case decides what is too much."""

    def __init__(self, sessions: async_sessionmaker[AsyncSession]) -> None:
        self._sessions = sessions

    async def admit(self, user_id: UUID) -> Admission:
        window = func.date_trunc("minute", func.now())
        insert = pg_insert(RateLimitRow).values(user_id=user_id, window_start=window, count=1)
        upsert = insert.on_conflict_do_update(
            index_elements=["user_id"],
            set_={
                "count": case(
                    (
                        RateLimitRow.window_start == insert.excluded.window_start,
                        RateLimitRow.count + 1,
                    ),
                    else_=1,
                ),
                "window_start": insert.excluded.window_start,
            },
        ).returning(RateLimitRow.count)
        tokens = select(func.coalesce(UsageDailyRow.tokens_in + UsageDailyRow.tokens_out, 0)).where(
            UsageDailyRow.user_id == user_id, UsageDailyRow.day == _TODAY_UTC
        )
        async with self._sessions.begin() as session:
            count = await session.scalar(upsert)
            tokens_today = await session.scalar(tokens)
        return Admission(requests_this_minute=count or 0, tokens_today=tokens_today or 0)

    async def record(self, user_id: UUID, usage: Usage | None) -> None:
        # No reply means nothing was reported: zero tokens, zero cost. A call whose cost is
        # unknown makes the day's cost NULL (NULL + x is NULL), which is the rule.
        tokens_in, tokens_out = (0, 0) if usage is None else (usage.tokens_in, usage.tokens_out)
        cost = Decimal(0) if usage is None else usage.cost
        insert = pg_insert(UsageDailyRow).values(
            user_id=user_id,
            day=_TODAY_UTC,
            requests=1,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost=cost,
        )
        upsert = insert.on_conflict_do_update(
            index_elements=["user_id", "day"],
            set_={
                "requests": UsageDailyRow.requests + insert.excluded.requests,
                "tokens_in": UsageDailyRow.tokens_in + insert.excluded.tokens_in,
                "tokens_out": UsageDailyRow.tokens_out + insert.excluded.tokens_out,
                "cost": UsageDailyRow.cost + insert.excluded.cost,
            },
        )
        async with self._sessions.begin() as session:
            await session.execute(upsert)

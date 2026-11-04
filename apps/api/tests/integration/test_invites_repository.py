import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from engleks_api.domain.access import (
    InvalidInvite,
    Redemption,
    UserStatus,
    generate_invite_code,
)
from engleks_api.domain.text_processing import Provider
from engleks_api.infra.db.repositories import SqlIdentityRepository, SqlInviteRepository


def new_id() -> str:
    return str(10**11 + uuid4().int % 10**11)


async def used_count(engine, code: str) -> int:
    async with engine.connect() as c:
        return await c.scalar(
            text("SELECT used_count FROM invite_code WHERE code = :c"), {"c": code}
        )


async def test_create_returns_invite(sessions):
    repo = SqlInviteRepository(sessions)
    code = generate_invite_code()
    invite = await repo.create(code, "Anna", 2, 7)
    assert (invite.code, invite.note, invite.max_uses, invite.used_count) == (code, "Anna", 2, 0)
    assert invite.revoked_at is None
    assert invite.expires_at is not None
    assert abs(invite.expires_at - (invite.created_at + timedelta(days=7))) < timedelta(seconds=5)


async def test_create_without_expiry(sessions):
    invite = await SqlInviteRepository(sessions).create(generate_invite_code(), None, 1, None)
    assert invite.expires_at is None and invite.note is None


async def test_redeem_registers_and_counts(sessions):
    repo = SqlInviteRepository(sessions)
    code, ext = generate_invite_code(), new_id()
    await repo.create(code, None, 2, None)
    redemption = await repo.redeem(code, Provider.TELEGRAM, ext)
    assert isinstance(redemption, Redemption)
    access = await SqlIdentityRepository(sessions).find_user(Provider.TELEGRAM, ext)
    assert access is not None
    assert (access.user_id, access.status) == (redemption.user_id, UserStatus.ACTIVE)
    async with sessions() as s:
        stored = (
            await s.execute(
                text(
                    "SELECT u.invite_code_id, i.id, i.used_count FROM app_user u, invite_code i "
                    "WHERE u.id = :u AND i.code = :c"
                ),
                {"u": redemption.user_id, "c": code},
            )
        ).one()
    assert stored.invite_code_id == redemption.invite_code_id == stored.id
    assert stored.used_count == 1


@pytest.mark.parametrize("reason", ["unknown", "expired", "revoked", "used_up"])
async def test_redeem_rejects_with_reason(sessions, reason):
    repo = SqlInviteRepository(sessions)
    code, ext = generate_invite_code(), new_id()
    if reason != "unknown":
        await repo.create(code, None, 1, None)
        async with sessions.begin() as s:
            if reason == "expired":
                sql = "UPDATE invite_code SET expires_at = now() - interval '1 second'"
            elif reason == "revoked":
                sql = "UPDATE invite_code SET revoked_at = now()"
            else:
                sql = None
            if sql:
                await s.execute(text(sql + " WHERE code = :c"), {"c": code})
        if reason == "used_up":
            await repo.redeem(code, Provider.TELEGRAM, new_id())
    with pytest.raises(InvalidInvite) as e:
        await repo.redeem(code, Provider.TELEGRAM, ext)
    assert e.value.reason == reason
    assert await SqlIdentityRepository(sessions).find_user(Provider.TELEGRAM, ext) is None


async def test_last_use_two_users_exactly_one_wins(engine):
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlInviteRepository(sessionmaker)
    code = generate_invite_code()
    await repo.create(code, None, 1, None)
    results = await asyncio.gather(
        repo.redeem(code, Provider.TELEGRAM, new_id()),
        repo.redeem(code, Provider.TELEGRAM, new_id()),
        return_exceptions=True,
    )
    wins = [r for r in results if isinstance(r, Redemption)]
    losses = [r for r in results if isinstance(r, InvalidInvite)]
    assert len(wins) == 1 and len(losses) == 1 and losses[0].reason == "used_up"
    assert await used_count(engine, code) == 1


async def test_same_user_concurrently_consumes_once(engine):
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
    repo = SqlInviteRepository(sessionmaker)
    code, ext = generate_invite_code(), new_id()
    await repo.create(code, None, 5, None)
    results = await asyncio.gather(
        repo.redeem(code, Provider.TELEGRAM, ext), repo.redeem(code, Provider.TELEGRAM, ext)
    )
    assert sorted(r is None for r in results) == [False, True]
    assert await used_count(engine, code) == 1
    async with engine.connect() as c:
        identities = await c.scalar(
            text("SELECT count(*) FROM identity WHERE external_id = :e"), {"e": ext}
        )
        users = await c.scalar(
            text(
                "SELECT count(*) FROM app_user WHERE id IN "
                "(SELECT user_id FROM identity WHERE external_id = :e)"
            ),
            {"e": ext},
        )
    assert (identities, users) == (1, 1)


async def test_revoke_marks_the_invite_and_unknown_is_false(sessions):
    repo = SqlInviteRepository(sessions)
    code = generate_invite_code()
    await repo.create(code, None, 1, None)
    assert await repo.revoke(code) is True
    assert await repo.revoke(code) is True
    assert await repo.revoke("NOSUCHCODE0") is False
    listed = {i.code: i for i in await repo.list_all()}
    assert listed[code].revoked_at is not None

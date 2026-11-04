from decimal import Decimal

from sqlalchemy import text

from engleks_api.domain.text_processing import Usage
from engleks_api.infra.db.repositories import SqlUsageLedger


def usage(tokens_in: int = 10, tokens_out: int = 5, cost: Decimal | None = Decimal("0.001")):
    return Usage("m", "process_text.v5", tokens_in, tokens_out, cost)


async def test_admit_counts_within_a_minute(sessions, register):
    user_id, _ = await register(sessions)
    ledger = SqlUsageLedger(sessions)
    admissions = [await ledger.admit(user_id) for _ in range(3)]
    assert [a.requests_this_minute for a in admissions] == [1, 2, 3]
    assert [a.tokens_today for a in admissions] == [0, 0, 0]


async def test_admit_resets_in_a_new_minute(sessions, register):
    user_id, _ = await register(sessions)
    ledger = SqlUsageLedger(sessions)
    await ledger.admit(user_id)
    await ledger.admit(user_id)
    async with sessions.begin() as session:
        await session.execute(
            text("UPDATE rate_limit SET window_start = window_start - interval '1 minute'")
        )
    assert (await ledger.admit(user_id)).requests_this_minute == 1


async def test_record_sums_across_calls(sessions, register):
    user_id, _ = await register(sessions)
    ledger = SqlUsageLedger(sessions)
    await ledger.record(user_id, usage())
    await ledger.record(user_id, usage())
    await ledger.record(user_id, None)
    async with sessions() as session:
        row = (
            await session.execute(
                text(
                    "SELECT requests, tokens_in, tokens_out, cost FROM usage_daily "
                    "WHERE user_id = :u AND day = (now() AT TIME ZONE 'UTC')::date"
                ),
                {"u": user_id},
            )
        ).one()
    assert tuple(row) == (3, 20, 10, Decimal("0.002"))
    assert (await ledger.admit(user_id)).tokens_today == 30


async def test_unknown_cost_makes_day_cost_null(sessions, register):
    user_id, _ = await register(sessions)
    ledger = SqlUsageLedger(sessions)
    await ledger.record(user_id, usage(cost=Decimal("0.001")))
    await ledger.record(user_id, usage(cost=None))
    await ledger.record(user_id, usage(cost=Decimal("0.001")))
    async with sessions() as session:
        cost = await session.scalar(
            text("SELECT cost FROM usage_daily WHERE user_id = :u"), {"u": user_id}
        )
    assert cost is None


async def test_rate_limit_table_one_row_per_user(sessions, register):
    user_id, _ = await register(sessions)
    ledger = SqlUsageLedger(sessions)
    for _ in range(3):
        await ledger.admit(user_id)
    async with sessions() as session:
        rows = await session.scalar(
            text("SELECT count(*) FROM rate_limit WHERE user_id = :u"), {"u": user_id}
        )
    assert rows == 1

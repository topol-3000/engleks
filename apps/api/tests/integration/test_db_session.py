from sqlalchemy import text


async def test_rollback_session_discards_changes(engine, rollback_session):
    async with rollback_session(engine) as s:
        await s.execute(text("CREATE TABLE rollback_probe (id int)"))
        await s.commit()  # commits the savepoint only
    async with engine.connect() as conn:
        exists = await conn.scalar(text("SELECT to_regclass('rollback_probe') IS NOT NULL"))
    assert exists is False

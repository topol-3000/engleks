import sqlalchemy as sa
from alembic import command


def test_upgrade_downgrade_and_check(postgres_url, alembic_config):
    cfg = alembic_config(postgres_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models and schema differ


async def test_schema_has_only_current_tables(engine):
    async with engine.connect() as c:
        names = await c.run_sync(lambda conn: sa.inspect(conn).get_table_names())
    assert sorted(names) == [
        "alembic_version",
        "app_user",
        "identity",
        "interaction",
        "invite_code",
        "rate_limit",
        "usage_daily",
    ]

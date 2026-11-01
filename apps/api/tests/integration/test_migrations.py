from alembic import command


def test_upgrade_downgrade_and_check(postgres_url, alembic_config):
    cfg = alembic_config(postgres_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    command.check(cfg)  # raises if models and schema differ

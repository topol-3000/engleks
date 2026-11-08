import asyncio
import os

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

import engleks_api.infra.db.models  # noqa: F401  (registers tables on Base.metadata)
from engleks_api.config import Settings
from engleks_api.infra.db.base import Base
from engleks_api.logging_setup import configure_logging

config = context.config

# Same log pipeline as the API (JSON in containers). Read from the environment
# directly: full Settings would require the DB connection string even for offline runs.
configure_logging(
    "json" if os.environ.get("API_LOG_FORMAT") == "json" else "console",
    os.environ.get("API_LOG_LEVEL", "INFO"),
)

target_metadata = Base.metadata


def database_url() -> str:
    return config.get_main_option("sqlalchemy.url") or Settings().database_url()  # pyright: ignore[reportCallIssue]


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    # Fail fast on an unreachable database instead of hanging the deploy.
    engine = create_async_engine(
        database_url(), poolclass=pool.NullPool, connect_args={"timeout": 10}
    )
    async with engine.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())

# pyright: reportCallIssue=false
# Pyright cannot see pydantic-settings init options (_env_file, _secrets_dir).
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from testcontainers.community.postgres import PostgresContainer

from engleks_api.config import Settings
from engleks_api.main import create_app

# Keep in sync with the postgres image in compose.yaml.
POSTGRES_IMAGE = (
    "postgres:18.6-trixie@sha256:3a4264520e121176619f54ff867d12774bc81e95e7b84db58d63bccdb6144b81"
)

ALEMBIC_INI = Path(__file__).parents[2] / "alembic.ini"

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx2.AsyncClient]]


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncGenerator[httpx2.AsyncClient]:
    transport = httpx2.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c
    finally:
        # ASGITransport does not run the lifespan, so dispose the engine here.
        await app.state.engine.dispose()


@pytest.fixture
def client_for() -> ClientFactory:
    return _client


@pytest.fixture
def app() -> FastAPI:
    return create_app(Settings(db_password=SecretStr("unused"), _env_file=None))


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[httpx2.AsyncClient]:
    async with _client(app) as c:
        yield c


@pytest.fixture(scope="session")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer(POSTGRES_IMAGE, driver="asyncpg") as pg:
        yield pg


@pytest.fixture(scope="session")
def postgres_url(postgres: PostgresContainer) -> str:
    return postgres.get_connection_url()


@pytest.fixture
def pg_settings(postgres: PostgresContainer) -> Settings:
    return Settings(
        db_host=postgres.get_container_host_ip(),
        db_port=int(postgres.get_exposed_port(5432)),
        db_user=postgres.username,
        db_password=SecretStr(postgres.password),
        db_name=postgres.dbname,
        _env_file=None,
    )


def _alembic_config(url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture
def alembic_config() -> Callable[[str], Config]:
    return _alembic_config


@pytest.fixture(scope="session")
def migrated_url(postgres_url: str) -> str:
    command.upgrade(_alembic_config(postgres_url), "head")
    return postgres_url


@pytest.fixture
async def engine(migrated_url: str) -> AsyncIterator[AsyncEngine]:
    # NullPool: no connections outlive the test's event loop.
    eng = create_async_engine(migrated_url, poolclass=NullPool)
    yield eng
    await eng.dispose()


@asynccontextmanager
async def _rollback_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session whose commits only release savepoints; everything is rolled back on exit."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()


@pytest.fixture
def rollback_session() -> Callable[[AsyncEngine], AbstractAsyncContextManager[AsyncSession]]:
    return _rollback_session


@pytest.fixture
async def db_session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    async with _rollback_session(engine) as session:
        yield session


@pytest.fixture
async def sessions(engine: AsyncEngine) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """A sessionmaker for repositories whose commits are savepoints, rolled back after the test."""
    async with engine.connect() as conn:
        transaction = await conn.begin()
        yield async_sessionmaker(
            bind=conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
        )
        await transaction.rollback()

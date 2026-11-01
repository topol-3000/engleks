# pyright: reportCallIssue=false
# Pyright cannot see pydantic-settings init options (_env_file, _secrets_dir).
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from testcontainers.community.postgres import PostgresContainer

from engleks_api.config import Settings
from engleks_api.main import create_app

# Keep in sync with the postgres image in compose.yaml.
POSTGRES_IMAGE = (
    "postgres:18.6-trixie@sha256:3a4264520e121176619f54ff867d12774bc81e95e7b84db58d63bccdb6144b81"
)

ClientFactory = Callable[[FastAPI], AbstractAsyncContextManager[httpx.AsyncClient]]


@asynccontextmanager
async def _client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    try:
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
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
async def client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
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

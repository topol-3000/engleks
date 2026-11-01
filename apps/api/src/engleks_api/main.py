from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from engleks_api.config import Settings
from engleks_api.infra.db.engine import build_engine
from engleks_api.infra.db.probe import DatabaseProbe
from engleks_api.logging_setup import configure_logging
from engleks_api.transport import health
from engleks_api.transport.errors import register_error_handlers
from engleks_api.transport.middleware import RequestIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # pyright: ignore[reportCallIssue]
    configure_logging(settings.log_format, settings.log_level)
    # Creating the engine does not connect, so the app can be built without a database.
    engine = build_engine(settings.database_url())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        await engine.dispose()

    app = FastAPI(title="Engleks API", lifespan=lifespan)
    app.state.engine = engine
    app.state.readiness_probes = [DatabaseProbe(engine)]
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    return app

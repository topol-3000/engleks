from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from engleks_api.config import Settings
from engleks_api.domain.access import Limits
from engleks_api.infra.db.engine import build_engine, build_sessionmaker
from engleks_api.infra.db.probe import DatabaseProbe
from engleks_api.infra.db.repositories import SqlIdentityRepository, SqlInteractionRepository
from engleks_api.infra.llm.proxy_rewriter import ProxyRewriter
from engleks_api.logging_setup import configure_logging
from engleks_api.transport import health, texts
from engleks_api.transport.errors import register_error_handlers
from engleks_api.transport.middleware import RequestIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # pyright: ignore[reportCallIssue]
    configure_logging(settings.log_format, settings.log_level)
    # Creating the engine does not connect, so the app can be built without a database.
    engine = build_engine(settings.database_url())
    sessions = build_sessionmaker(engine)
    rewriter = ProxyRewriter(
        settings.llm_base_url,
        settings.llm_api_key.get_secret_value(),
        settings.llm_model,
        settings.llm_timeout_s,
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        yield
        await rewriter.aclose()
        await engine.dispose()

    app = FastAPI(title="Engleks API", lifespan=lifespan)
    app.state.engine = engine
    app.state.readiness_probes = [DatabaseProbe(engine)]
    app.state.identities = SqlIdentityRepository(sessions)
    app.state.interactions = SqlInteractionRepository(sessions)
    app.state.rewriter = rewriter
    app.state.service_token = settings.service_token
    app.state.default_limits = Limits(settings.rate_limit_per_minute, settings.daily_token_budget)
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    app.include_router(texts.router)
    return app

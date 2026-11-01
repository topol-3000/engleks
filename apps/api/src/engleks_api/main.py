from fastapi import FastAPI

from engleks_api.config import Settings
from engleks_api.logging_setup import configure_logging
from engleks_api.transport import health
from engleks_api.transport.errors import register_error_handlers
from engleks_api.transport.middleware import RequestIdMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()  # pyright: ignore[reportCallIssue]
    configure_logging(settings.log_format, settings.log_level)

    app = FastAPI(title="Engleks API")
    app.add_middleware(RequestIdMiddleware)
    register_error_handlers(app)
    app.include_router(health.router)
    return app

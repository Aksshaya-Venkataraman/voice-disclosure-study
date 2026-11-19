from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.engine import Engine as SAEngine
from starlette.middleware.trustedhost import TrustedHostMiddleware

from gateway import __version__
from gateway.asr import GoogleSpeechToText, SpeechToText
from gateway.config import Settings, get_settings
from gateway.db import make_engine, make_session_factory
from gateway.engine import Engine, RasaEngine, ScriptedEngine
from gateway.routers import participant, researcher
from gateway.security import RateLimiter, SecurityMiddleware, configure_logging

log = logging.getLogger("gateway")
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def build_engine(settings: Settings) -> Engine:
    if settings.interview_engine == "rasa":
        return RasaEngine(settings.rasa_url, settings.rasa_timeout_seconds)
    return ScriptedEngine()


def build_asr(settings: Settings) -> SpeechToText | None:
    if settings.asr_provider == "google":
        return GoogleSpeechToText(settings.asr_language)
    return None


def create_app(
    settings: Settings | None = None,
    *,
    db_engine: SAEngine | None = None,
    engine: Engine | None = None,
    asr: SpeechToText | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    db_engine = db_engine or make_engine(settings.database_url.get_secret_value())

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        yield
        db_engine.dispose()

    app = FastAPI(
        title="Voice disclosure study",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        debug=False,
    )
    app.state.settings = settings
    app.state.session_factory = make_session_factory(db_engine)
    app.state.engine = engine or build_engine(settings)
    app.state.asr = asr if asr is not None else build_asr(settings)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_storage_uri.get_secret_value())

    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    app.add_middleware(
        SecurityMiddleware, hsts=settings.environment == "production", max_body_bytes=settings.max_audio_bytes + 64_000
    )

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error", exc_info=exc)
        return JSONResponse({"detail": "internal server error"}, status_code=500)

    @app.get("/api/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/ready", tags=["ops"])
    def ready() -> JSONResponse:
        try:
            with db_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return JSONResponse(
            {
                "status": "ready",
                "engine": getattr(app.state.engine, "name", "custom"),
                "speech_input": settings.asr_provider != "disabled",
            }
        )

    app.include_router(participant.router)
    app.include_router(researcher.router)
    # The participant web app (static files) is served last so /api routes win.
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


def app_factory() -> FastAPI:  # uvicorn --factory gateway.main:app_factory
    return create_app()

"""FastAPI application factory and entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from app.api.health import router as health_router
from app.api.import_export import router as import_export_router
from app.api.versions import router as versions_router
from app.api.workflows import router as workflows_router
from app.config import Settings, get_settings
from app.db.indexes import ensure_indexes
from app.db.mongo import MongoManager
from app.errors import (
    ConflictError,
    NotFoundError,
    OperationNotAllowedError,
    ValidationError,
)
from app.logging_setup import setup_logging


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.log_level)
    log = logging.getLogger("app")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        log.info(
            "starting %s v%s env=%s host=%s port=%s",
            settings.app_name,
            settings.app_version,
            settings.env,
            settings.host,
            settings.port,
        )

        mongo = MongoManager(
            uri=settings.mongodb_uri,
            db_name=settings.mongodb_db,
            connect_timeout_ms=settings.mongodb_connect_timeout_ms,
            server_selection_timeout_ms=settings.mongodb_server_selection_timeout_ms,
        )
        app.state.mongo = mongo

        if settings.mongodb_skip_startup:
            log.warning("MONGODB_SKIP_STARTUP=true — skipping ping/index creation")
        else:
            try:
                await mongo.ping()
                await ensure_indexes(mongo.db)
                log.info("mongodb ready db=%s", settings.mongodb_db)
            except PyMongoError as e:
                # Non-fatal: client is already constructed and will retry on
                # the next operation. /health/db reports the failure.
                log.warning(
                    "mongodb not reachable at startup (%s). The service is up; "
                    "/health/db will return 503 until the connection succeeds.",
                    e,
                )

        try:
            yield
        finally:
            await mongo.close()
            log.info("shutdown complete")

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routers — order: import_export and versions before workflows so the
    # static-segment routes (/import, /{id}/versions/...) are registered before
    # /{workflow_id} catches them.
    app.include_router(health_router)
    app.include_router(import_export_router)
    app.include_router(versions_router)
    app.include_router(workflows_router)

    _register_exception_handlers(app)

    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """Map domain errors to consistent JSON HTTP responses."""

    def _payload(detail: str, code: str, **extras) -> dict:
        body = {"detail": detail, "code": code}
        body.update(extras)
        return body

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(
            status_code=404, content=_payload(str(exc), "NOT_FOUND")
        )

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(
            status_code=409, content=_payload(str(exc), "CONFLICT")
        )

    @app.exception_handler(ValidationError)
    async def _validation(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=_payload(
                str(exc),
                "VALIDATION_FAILED",
                issues=[i.model_dump(mode="json") for i in exc.issues],
            ),
        )

    @app.exception_handler(OperationNotAllowedError)
    async def _not_allowed(_: Request, exc: OperationNotAllowedError) -> JSONResponse:
        return JSONResponse(
            status_code=403, content=_payload(str(exc), "OPERATION_NOT_ALLOWED")
        )


app = create_app()


def main() -> None:
    """Console entrypoint: ``python -m app.main``."""
    import uvicorn

    settings = get_settings()
    is_dev = settings.env == "dev"
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=is_dev,
        reload_dirs=["app"] if is_dev else None,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()

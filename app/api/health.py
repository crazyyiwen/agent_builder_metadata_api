"""Liveness + readiness endpoints.

- ``GET /health``    — process is up. Always 200 if the app is serving.
- ``GET /health/db`` — MongoDB ping. 200 with details on success, 503 on
                       any PyMongo error (network, auth, server selection).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pymongo.errors import PyMongoError

from app.config import Settings, get_settings
from app.db.mongo import MongoManager
from app.deps import get_mongo

router = APIRouter(tags=["health"])

log = logging.getLogger("app.api.health")


@router.get("/health")
def health(settings: Settings = Depends(get_settings)) -> dict:
    return {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "env": settings.env,
    }


@router.get("/health/db")
async def health_db(mongo: MongoManager = Depends(get_mongo)) -> dict:
    try:
        ping = await mongo.ping()
        info = await mongo.server_info()
    except PyMongoError as e:
        log.warning("mongodb health check failed: %s", e)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"status": "error", "mongodb": {"error": str(e)}},
        )
    return {
        "status": "ok",
        "mongodb": {
            "ok": ping.get("ok"),
            "db": mongo.db_name,
            "server_version": info.get("version"),
        },
    }

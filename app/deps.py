"""FastAPI dependencies. Pull shared resources off ``request.app.state`` and
wire up the repo+service tree per request."""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, Request

from app.config import Settings, get_settings
from app.db.mongo import MongoManager
from app.db.repos.audit_log_repo import WorkflowAuditLogRepository
from app.db.repos.version_repo import WorkflowVersionRepository
from app.db.repos.workflow_repo import WorkflowRepository
from app.services.import_export_service import ImportExportService
from app.services.version_service import WorkflowVersionService
from app.services.workflow_service import WorkflowService

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase


def get_mongo(request: Request) -> MongoManager:
    mongo: MongoManager | None = getattr(request.app.state, "mongo", None)
    if mongo is None:
        raise RuntimeError("MongoManager not initialized on app.state")
    return mongo


def get_db(request: Request) -> "AsyncDatabase":
    return get_mongo(request).db


# ---------- repositories ----------


def get_workflow_repo(db=Depends(get_db)) -> WorkflowRepository:
    return WorkflowRepository(db)


def get_version_repo(db=Depends(get_db)) -> WorkflowVersionRepository:
    return WorkflowVersionRepository(db)


def get_audit_repo(db=Depends(get_db)) -> WorkflowAuditLogRepository:
    return WorkflowAuditLogRepository(db)


# ---------- services ----------


def get_workflow_service(
    workflow_repo: WorkflowRepository = Depends(get_workflow_repo),
    version_repo: WorkflowVersionRepository = Depends(get_version_repo),
    audit_repo: WorkflowAuditLogRepository = Depends(get_audit_repo),
    settings: Settings = Depends(get_settings),
) -> WorkflowService:
    return WorkflowService(
        workflow_repo=workflow_repo,
        version_repo=version_repo,
        audit_repo=audit_repo,
        allow_hard_delete=settings.enable_hard_delete,
    )


def get_version_service(
    version_repo: WorkflowVersionRepository = Depends(get_version_repo),
    workflow_service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowVersionService:
    return WorkflowVersionService(
        version_repo=version_repo,
        workflow_service=workflow_service,
    )


def get_import_export_service(
    workflow_service: WorkflowService = Depends(get_workflow_service),
    version_repo: WorkflowVersionRepository = Depends(get_version_repo),
) -> ImportExportService:
    return ImportExportService(
        workflow_service=workflow_service,
        version_repo=version_repo,
    )

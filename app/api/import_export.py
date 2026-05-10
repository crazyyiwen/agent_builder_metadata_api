"""Import + export routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Depends, Header, Query

from app.deps import get_import_export_service
from app.models.responses import ExportResponse, ImportResponse
from app.services.import_export_service import ImportExportService

router = APIRouter(prefix="/api/workflows", tags=["import-export"])


@router.post(
    "/import",
    response_model=ImportResponse,
    summary=(
        "Import a workflow JSON. Pass replace_workflow_id to update an "
        "existing workflow; otherwise a new one is created."
    ),
)
async def import_workflow(
    payload: dict[str, Any] = Body(..., description="WorkflowDoc JSON or wrapping envelope"),
    replace_workflow_id: str | None = Query(default=None),
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: ImportExportService = Depends(get_import_export_service),
) -> ImportResponse:
    return await service.import_workflow(
        payload,
        replace_workflow_id=replace_workflow_id,
        actor_id=actor_id,
    )


@router.get(
    "/{workflow_id}/export",
    response_model=ExportResponse,
    summary="Export the latest workflow doc as a self-contained JSON envelope",
)
async def export_latest(
    workflow_id: str,
    service: ImportExportService = Depends(get_import_export_service),
) -> ExportResponse:
    return await service.export_latest(workflow_id)


@router.get(
    "/{workflow_id}/versions/{version}/export",
    response_model=ExportResponse,
    summary="Export a specific historical version as JSON",
)
async def export_version(
    workflow_id: str,
    version: int,
    service: ImportExportService = Depends(get_import_export_service),
) -> ExportResponse:
    return await service.export_version(workflow_id, version)

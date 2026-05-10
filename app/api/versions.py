"""Workflow version routes: list / get / snapshot / rollback."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_version_service
from app.models.responses import WorkflowResponse, WorkflowVersionResponse
from app.services.version_service import VersionListResponse, WorkflowVersionService

router = APIRouter(prefix="/api/workflows", tags=["versions"])


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_note: str | None = Field(default=None, max_length=500)


class RollbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    change_note: str | None = Field(default=None, max_length=500)


@router.get(
    "/{workflow_id}/versions",
    response_model=VersionListResponse,
    summary="Paginated list of versions, newest first",
)
async def list_versions(
    workflow_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    service: WorkflowVersionService = Depends(get_version_service),
) -> VersionListResponse:
    return await service.list_versions(workflow_id, page=page, page_size=page_size)


@router.get(
    "/{workflow_id}/versions/{version}",
    response_model=WorkflowVersionResponse,
)
async def get_version(
    workflow_id: str,
    version: int,
    service: WorkflowVersionService = Depends(get_version_service),
) -> WorkflowVersionResponse:
    return await service.get_version(workflow_id, version)


@router.post(
    "/{workflow_id}/versions",
    response_model=WorkflowVersionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Snapshot the current head as a new version row",
)
async def create_version_snapshot(
    workflow_id: str,
    request: SnapshotRequest = Body(default_factory=SnapshotRequest),
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowVersionService = Depends(get_version_service),
) -> WorkflowVersionResponse:
    return await service.create_snapshot(
        workflow_id,
        change_note=request.change_note,
        actor_id=actor_id,
    )


@router.post(
    "/{workflow_id}/versions/{version}/rollback",
    response_model=WorkflowResponse,
    summary="Promote a historical version's doc to head (writes a new version)",
)
async def rollback_to_version(
    workflow_id: str,
    version: int,
    request: RollbackRequest = Body(default_factory=RollbackRequest),
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowVersionService = Depends(get_version_service),
) -> WorkflowResponse:
    return await service.rollback_to(
        workflow_id,
        target_version=version,
        change_note=request.change_note,
        actor_id=actor_id,
    )

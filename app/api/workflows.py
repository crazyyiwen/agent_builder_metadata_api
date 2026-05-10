"""Workflow CRUD + actions + validation routes.

Routes are intentionally thin: parse input → call a service → return the
result. All business logic lives in ``app.services``. Domain exceptions
(NotFoundError, ConflictError, ValidationError, OperationNotAllowedError)
bubble up to handlers registered in ``app.main``.

Path-ordering note: static-segment routes (``/validate``, ``/import``)
are declared before parameterized ones (``/{workflow_id}``) so FastAPI's
in-order matching doesn't route ``POST /workflows/validate`` to the
parameterized handler with ``workflow_id="validate"``.
"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.deps import get_workflow_service
from app.models.common import ValidationResult
from app.models.meta import WorkflowMeta, WorkflowStatus
from app.models.requests import (
    CreateWorkflowRequest,
    PatchWorkflowRequest,
    UpdateWorkflowRequest,
)
from app.models.responses import WorkflowListResponse, WorkflowResponse
from app.models.summary import ValidationStatus
from app.models.workflow import WorkflowDoc
from app.services.workflow_service import WorkflowService

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


# ---------- request body shims ----------


class DuplicateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_name: str | None = Field(default=None, max_length=200)
    owner_id: str | None = Field(default=None, max_length=128)


# ---------- helpers ----------


def _parse_if_match(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    raw = if_match.strip().strip('"').strip("'")
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"detail": f"If-Match must be a numeric version, got {if_match!r}"},
        ) from e


# ---------- static-segment routes (must come BEFORE /{workflow_id}) ----------


@router.post(
    "/validate",
    response_model=ValidationResult,
    summary="Stateless validation of a workflow doc",
)
async def validate_doc(
    doc: WorkflowDoc,
    service: WorkflowService = Depends(get_workflow_service),
) -> ValidationResult:
    return service.validate_doc(doc)


# ---------- collection routes ----------


@router.post(
    "",
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new workflow",
)
async def create_workflow(
    request: CreateWorkflowRequest,
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    return await service.create_workflow(request, actor_id=actor_id)


@router.get(
    "",
    response_model=WorkflowListResponse,
    summary="Paginated list of workflows",
)
async def list_workflows(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    q: str | None = Query(None, description="Substring search on name (case-insensitive)"),
    owner_id: str | None = None,
    status_: WorkflowStatus | None = Query(None, alias="status"),
    tag: str | None = None,
    category: str | None = None,
    node_type: str | None = None,
    validation_status: ValidationStatus | None = None,
    include_deleted: bool = False,
    sort_by: str = Query("updated_at", pattern=r"^(updated_at|created_at|name|current_version)$"),
    sort_order: str = Query("desc", pattern=r"^(asc|desc)$"),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowListResponse:
    return await service.list_workflows(
        page=page,
        page_size=page_size,
        name_contains=q,
        owner_id=owner_id,
        status=status_,
        tag=tag,
        category=category,
        node_type=node_type,
        validation_status=validation_status.value if validation_status else None,
        include_archived=include_deleted,
        sort_by=sort_by,
        sort_order=sort_order,
    )


# ---------- per-workflow routes ----------


@router.get("/{workflow_id}", response_model=WorkflowResponse)
async def get_workflow(
    workflow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    return await service.get_workflow(workflow_id)


@router.put(
    "/{workflow_id}",
    response_model=WorkflowResponse,
    summary="Replace the workflow doc; bumps version. Use If-Match header for OCC.",
)
async def update_workflow(
    workflow_id: str,
    request: UpdateWorkflowRequest,
    if_match: str | None = Header(default=None, alias="If-Match"),
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    expected_version = _parse_if_match(if_match)
    return await service.update_workflow(
        workflow_id,
        request,
        expected_version=expected_version,
        actor_id=actor_id,
    )


@router.patch(
    "/{workflow_id}",
    response_model=WorkflowMeta,
    summary="Metadata-only update; does not bump version",
)
async def patch_workflow(
    workflow_id: str,
    request: PatchWorkflowRequest,
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowMeta:
    return await service.patch_workflow(workflow_id, request, actor_id=actor_id)


@router.delete(
    "/{workflow_id}",
    response_model=WorkflowMeta,
    summary="Soft delete (archive). Use /hard-delete to permanently remove.",
)
async def soft_delete_workflow(
    workflow_id: str,
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowMeta:
    return await service.soft_delete_workflow(workflow_id, actor_id=actor_id)


@router.delete(
    "/{workflow_id}/hard-delete",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete the workflow + all versions + audit logs",
)
async def hard_delete_workflow(
    workflow_id: str,
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowService = Depends(get_workflow_service),
) -> None:
    await service.hard_delete_workflow(workflow_id, actor_id=actor_id)


@router.post("/{workflow_id}/restore", response_model=WorkflowMeta)
async def restore_workflow(
    workflow_id: str,
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowMeta:
    return await service.restore_workflow(workflow_id, actor_id=actor_id)


@router.post("/{workflow_id}/archive", response_model=WorkflowMeta)
async def archive_workflow(
    workflow_id: str,
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowMeta:
    return await service.archive_workflow(workflow_id, actor_id=actor_id)


@router.post("/{workflow_id}/unarchive", response_model=WorkflowMeta)
async def unarchive_workflow(
    workflow_id: str,
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowMeta:
    return await service.unarchive_workflow(workflow_id, actor_id=actor_id)


@router.post(
    "/{workflow_id}/duplicate",
    response_model=WorkflowResponse,
    status_code=status.HTTP_201_CREATED,
)
async def duplicate_workflow(
    workflow_id: str,
    request: DuplicateRequest = Body(default_factory=DuplicateRequest),
    actor_id: str | None = Header(default=None, alias="X-Actor-Id"),
    service: WorkflowService = Depends(get_workflow_service),
) -> WorkflowResponse:
    return await service.duplicate_workflow(
        workflow_id,
        new_name=request.new_name,
        owner_id=request.owner_id,
        actor_id=actor_id,
    )


@router.post(
    "/{workflow_id}/validate",
    response_model=ValidationResult,
    summary="Validate the stored workflow doc without modifying it",
)
async def validate_existing_workflow(
    workflow_id: str,
    service: WorkflowService = Depends(get_workflow_service),
) -> ValidationResult:
    response = await service.get_workflow(workflow_id)
    return service.validate_doc(response.doc)

"""Response bodies for workflow APIs.

Every response model uses ``extra='forbid'`` so we never accidentally
construct one from a Mongo cursor row that still contains ``_id``.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.common import PaginationMeta, ValidationResult
from app.models.meta import WorkflowMeta
from app.models.workflow import WorkflowDoc


class WorkflowResponse(BaseModel):
    """Single workflow record: metadata + the latest doc."""

    model_config = ConfigDict(extra="forbid")

    meta: WorkflowMeta
    doc: WorkflowDoc


class WorkflowListResponse(BaseModel):
    """Paginated list of workflow metadata. The full doc is intentionally
    omitted from list views — fetch ``GET /workflows/{id}`` for the doc."""

    model_config = ConfigDict(extra="forbid")

    items: list[WorkflowMeta]
    pagination: PaginationMeta


class WorkflowVersionSummary(BaseModel):
    """One row in a version-history listing. Doc omitted for compactness."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    version: int = Field(ge=1)
    change_note: str | None = None
    created_at: datetime
    created_by: str | None = None
    valid: bool | None = None


class WorkflowVersionResponse(BaseModel):
    """A specific historical version, with its full doc."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    version: int = Field(ge=1)
    doc: WorkflowDoc
    change_note: str | None = None
    validation: ValidationResult | None = None
    created_at: datetime
    created_by: str | None = None


class ImportResponse(BaseModel):
    """Result of importing a workflow JSON."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    created: bool = Field(
        description="True if a new workflow was created; False if an existing one was updated",
    )
    version: int = Field(ge=1)
    validation: ValidationResult


class ExportResponse(BaseModel):
    """Self-contained payload suitable for re-import. Mirrors the React
    ``exportWorkflowToFile`` JSON envelope."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    version: int = Field(ge=1)
    exported_at: datetime
    doc: WorkflowDoc

"""Request bodies for workflow APIs."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.meta import WorkflowStatus
from app.models.workflow import WorkflowDoc


class CreateWorkflowRequest(BaseModel):
    """``POST /workflows``. ``doc`` is optional — if omitted the server
    creates an empty workflow with just a Start node."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] = Field(default_factory=list)
    category: str | None = Field(default=None, max_length=100)
    doc: WorkflowDoc | None = None
    change_note: str | None = Field(default=None, max_length=500)


class UpdateWorkflowRequest(BaseModel):
    """``PUT /workflows/{id}``. Full-doc replace → bumps to a new version."""

    model_config = ConfigDict(extra="forbid")

    doc: WorkflowDoc
    change_note: str | None = Field(default=None, max_length=500)


class PatchWorkflowRequest(BaseModel):
    """``PATCH /workflows/{id}``. Metadata-only update; never bumps version.

    Use ``model_dump(exclude_unset=True)`` to distinguish "field omitted"
    from "field explicitly set to null/empty".
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = None
    category: str | None = Field(default=None, max_length=100)
    status: WorkflowStatus | None = None

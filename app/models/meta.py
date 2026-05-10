"""Workflow head-document metadata.

These are the fields in the ``workflows`` collection's head document, minus
the embedded ``latestDoc``. The model is strictly typed with ``extra='forbid'``
so MongoDB internals (``_id``, raw cursor fields) cannot leak into API
responses.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class WorkflowStatus(str, Enum):
    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class WorkflowMeta(BaseModel):
    """List-view fields. Mirrors the head document; never exposes ``_id``."""

    model_config = ConfigDict(extra="forbid")

    workflow_id: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$",
        description="Stable, URL-safe public identifier",
    )
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    status: WorkflowStatus = WorkflowStatus.DRAFT
    current_version: int = Field(ge=1)
    owner_id: str | None = Field(default=None, max_length=128)
    tags: list[str] = Field(default_factory=list)
    category: str | None = Field(default=None, max_length=100)
    node_types: list[str] = Field(
        default_factory=list,
        description=(
            "Distinct node type keys present in the workflow, denormalized "
            "from doc.nodes for fast filter queries"
        ),
    )
    created_at: datetime
    updated_at: datetime

"""Shared response types: validation results, pagination."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"


class ValidationIssue(BaseModel):
    """A single validation finding from the workflow validator."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(
        description="Dotted path into the workflow doc, e.g. 'nodes[2].data.config.model'",
    )
    message: str
    severity: ValidationSeverity = ValidationSeverity.ERROR
    code: str | None = Field(default=None, description="Short rule identifier")


class ValidationResult(BaseModel):
    """Outcome of a validation pass over a WorkflowDoc."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    issues: list[ValidationIssue] = Field(default_factory=list)


class PaginationMeta(BaseModel):
    """Page bookkeeping for list endpoints. The wire format is page-based
    (``page`` / ``page_size``) — services translate to ``limit``/``offset``
    internally for Mongo skip/limit calls."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=200)
    has_more: bool

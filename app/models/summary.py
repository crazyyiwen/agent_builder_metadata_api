"""Workflow summary computed by the validation service.

The summary denormalizes a few cheap-to-compute facts about a workflow doc
so list/search endpoints can render badges (counts, status) without
re-running validation in the request hot path.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class ValidationStatus(str, Enum):
    VALID = "valid"
    WARNING = "warning"
    ERROR = "error"


class WorkflowSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_count: int = Field(ge=0)
    edge_count: int = Field(ge=0)
    node_types: list[str] = Field(
        description="Sorted distinct node-type keys present in the workflow",
    )
    has_start_node: bool
    has_output_node: bool
    start_node_count: int = Field(ge=0)
    output_node_count: int = Field(ge=0)
    validation_status: ValidationStatus
    validation_errors: int = Field(ge=0)
    validation_warnings: int = Field(ge=0)

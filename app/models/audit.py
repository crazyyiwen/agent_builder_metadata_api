"""Audit-log entry shape.

Audit log rows record state-changing actions on workflows: create, update,
patch, archive/restore, delete, rollback, duplicate. The repository never
mutates rows; logs are append-only.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AuditAction(str, Enum):
    CREATED = "created"
    UPDATED = "updated"
    PATCHED = "patched"
    ARCHIVED = "archived"
    RESTORED = "restored"
    DELETED = "deleted"           # hard delete
    ROLLED_BACK = "rolled_back"
    DUPLICATED = "duplicated"


class AuditLogEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workflow_id: str
    action: AuditAction
    actor_id: str | None = None
    version: int | None = Field(default=None, ge=1)
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime

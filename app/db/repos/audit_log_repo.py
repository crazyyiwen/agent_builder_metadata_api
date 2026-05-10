"""Repository for the ``workflow_audit_logs`` collection.

Append-only log of state-changing actions on workflows. Sort order is
newest-first via the ``(workflow_id asc, created_at desc)`` index.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.db import collections as col
from app.models.audit import AuditAction, AuditLogEntry

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase


def _now() -> datetime:
    return datetime.now(timezone.utc)


_AUDIT_FIELDS = (
    "workflow_id",
    "action",
    "actor_id",
    "version",
    "details",
    "created_at",
)


def _entry_from_record(record: dict) -> AuditLogEntry:
    return AuditLogEntry.model_validate({k: record.get(k) for k in _AUDIT_FIELDS})


class WorkflowAuditLogRepository:
    def __init__(self, db: "AsyncDatabase") -> None:
        self._coll = db[col.WORKFLOW_AUDIT_LOGS]

    async def write(
        self,
        *,
        workflow_id: str,
        action: AuditAction | str,
        actor_id: str | None = None,
        version: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> AuditLogEntry:
        record = {
            "workflow_id": workflow_id,
            "action": action.value if isinstance(action, AuditAction) else action,
            "actor_id": actor_id,
            "version": version,
            "details": dict(details or {}),
            "created_at": _now(),
        }
        await self._coll.insert_one(record)
        return _entry_from_record(record)

    async def list(
        self,
        workflow_id: str,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[AuditLogEntry], int]:
        """Newest-first. Returns ``(items, total_count)``."""
        query = {"workflow_id": workflow_id}
        total = await self._coll.count_documents(query)
        projection = {k: 1 for k in _AUDIT_FIELDS}
        cursor = (
            self._coll.find(query, projection=projection)
            # Stable tie-break: writes within the same millisecond would
            # otherwise sort in implementation-defined order.
            .sort([("created_at", -1), ("_id", -1)])
            .skip(offset)
            .limit(limit)
        )
        items: list[AuditLogEntry] = []
        async for record in cursor:
            items.append(_entry_from_record(record))
        return items, total

    async def delete_all_for_workflow(self, workflow_id: str) -> int:
        """Cascade helper invoked by the workflow hard-delete service."""
        result = await self._coll.delete_many({"workflow_id": workflow_id})
        return result.deleted_count

"""Repository for the ``workflow_versions`` collection.

Versions are append-only — they are never updated or deleted except via
the cascade helper called by a workflow hard-delete. Rollback support is
provided by ``get`` (fetch a historical doc) plus the workflow-repo's
``update_head`` (the orchestrating service writes a new version that
copies the old doc forward).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from pymongo.errors import DuplicateKeyError

from app.db import collections as col
from app.errors import ConflictError
from app.models.common import ValidationResult
from app.models.responses import WorkflowVersionResponse, WorkflowVersionSummary
from app.models.summary import WorkflowSummary
from app.models.workflow import WorkflowDoc

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WorkflowVersionRepository:
    def __init__(self, db: "AsyncDatabase") -> None:
        self._coll = db[col.WORKFLOW_VERSIONS]

    async def create(
        self,
        *,
        workflow_id: str,
        version: int,
        doc: WorkflowDoc,
        change_note: str | None = None,
        validation: ValidationResult | None = None,
        summary: WorkflowSummary | None = None,
        created_by: str | None = None,
    ) -> WorkflowVersionResponse:
        now = _now()
        # Server-authoritative: stamp doc.id to match workflow_id.
        doc_payload = doc.model_copy(update={"id": workflow_id}).model_dump(mode="json")
        record = {
            "workflow_id": workflow_id,
            "version": version,
            "doc": doc_payload,
            "change_note": change_note,
            "validation": validation.model_dump(mode="json") if validation else None,
            "summary": summary.model_dump(mode="json") if summary else None,
            "created_at": now,
            "created_by": created_by,
        }
        try:
            await self._coll.insert_one(record)
        except DuplicateKeyError as e:
            raise ConflictError(
                f"version {version} already exists for workflow {workflow_id!r}"
            ) from e
        return WorkflowVersionResponse(
            workflow_id=workflow_id,
            version=version,
            doc=WorkflowDoc.model_validate(doc_payload),
            change_note=change_note,
            validation=validation,
            created_at=now,
            created_by=created_by,
        )

    async def get(
        self,
        workflow_id: str,
        version: int,
    ) -> WorkflowVersionResponse | None:
        record = await self._coll.find_one(
            {"workflow_id": workflow_id, "version": version}
        )
        if record is None:
            return None
        validation = (
            ValidationResult.model_validate(record["validation"])
            if record.get("validation")
            else None
        )
        return WorkflowVersionResponse(
            workflow_id=record["workflow_id"],
            version=record["version"],
            doc=WorkflowDoc.model_validate(record["doc"]),
            change_note=record.get("change_note"),
            validation=validation,
            created_at=record["created_at"],
            created_by=record.get("created_by"),
        )

    async def list_summaries(
        self,
        workflow_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[WorkflowVersionSummary], int]:
        """Newest version first. Doc omitted; use ``get`` for the full record."""
        query = {"workflow_id": workflow_id}
        total = await self._coll.count_documents(query)
        projection = {
            "workflow_id": 1,
            "version": 1,
            "change_note": 1,
            "created_at": 1,
            "created_by": 1,
            "validation.valid": 1,
        }
        cursor = (
            self._coll.find(query, projection=projection)
            .sort("version", -1)
            .skip(offset)
            .limit(limit)
        )
        items: list[WorkflowVersionSummary] = []
        async for record in cursor:
            valid = None
            if record.get("validation"):
                valid = record["validation"].get("valid")
            items.append(
                WorkflowVersionSummary(
                    workflow_id=record["workflow_id"],
                    version=record["version"],
                    change_note=record.get("change_note"),
                    created_at=record["created_at"],
                    created_by=record.get("created_by"),
                    valid=valid,
                )
            )
        return items, total

    async def get_max_version(self, workflow_id: str) -> int | None:
        """Largest version number, or None if no versions exist for the workflow."""
        record = await self._coll.find_one(
            {"workflow_id": workflow_id},
            sort=[("version", -1)],
            projection={"version": 1},
        )
        return record["version"] if record else None

    async def get_doc_for_rollback(
        self,
        workflow_id: str,
        version: int,
    ) -> WorkflowDoc | None:
        """Fetch only the doc payload of a historical version. Used by the
        rollback service which then writes a new version copying it forward."""
        record = await self._coll.find_one(
            {"workflow_id": workflow_id, "version": version},
            projection={"doc": 1},
        )
        if record is None:
            return None
        return WorkflowDoc.model_validate(record["doc"])

    async def delete_all_for_workflow(self, workflow_id: str) -> int:
        """Cascade helper: removes every version row for the workflow.
        Returns the deleted row count."""
        result = await self._coll.delete_many({"workflow_id": workflow_id})
        return result.deleted_count

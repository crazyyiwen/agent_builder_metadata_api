"""Repository for the ``workflows`` collection (head documents).

The head document holds the latest snapshot of a workflow plus its public
metadata. Version history lives in ``workflow_versions``; cascading deletes
across collections are the orchestrating service's responsibility, not this
repo's.

Key behaviors:
- ``workflow_id`` is the public identifier; the Mongo ``_id`` is hidden.
- ``current_version`` is bumped on each ``update_head`` and used as the
  optimistic-concurrency token.
- ``status`` is one of WorkflowStatus values; archive/unarchive are the
  preferred verbs (``soft_delete`` and ``restore`` are aliases).
- Listings exclude archived workflows by default (toggle via
  ``include_archived=True``).
"""

from __future__ import annotations

import re
import secrets
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.db import collections as col
from app.errors import ConflictError, NotFoundError
from app.models.meta import WorkflowMeta, WorkflowStatus
from app.models.responses import WorkflowResponse
from app.models.summary import WorkflowSummary
from app.models.workflow import WorkflowDoc

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase


META_FIELDS = (
    "workflow_id",
    "name",
    "description",
    "status",
    "current_version",
    "owner_id",
    "tags",
    "category",
    "node_types",
    "created_at",
    "updated_at",
)


def generate_workflow_id() -> str:
    """``wf_<11 url-safe chars>``. ~64 bits of entropy; collision-resistant."""
    return f"wf_{secrets.token_urlsafe(8)}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _meta_from_record(record: dict) -> WorkflowMeta:
    return WorkflowMeta.model_validate({k: record.get(k) for k in META_FIELDS})


def _doc_from_record(record: dict) -> WorkflowDoc:
    raw = dict(record.get("latest_doc") or {})
    return WorkflowDoc.model_validate(raw)


def _stamp_doc_id(doc: WorkflowDoc, workflow_id: str) -> dict:
    """Server-authoritative: replace doc.id with workflow_id before persisting."""
    return doc.model_copy(update={"id": workflow_id}).model_dump(mode="json")


class WorkflowRepository:
    def __init__(self, db: "AsyncDatabase") -> None:
        self._coll = db[col.WORKFLOWS]

    # ---------- create ----------

    async def create(
        self,
        *,
        name: str,
        doc: WorkflowDoc,
        summary: WorkflowSummary,
        description: str | None = None,
        tags: list[str] | None = None,
        category: str | None = None,
        owner_id: str | None = None,
        status: WorkflowStatus = WorkflowStatus.DRAFT,
        workflow_id: str | None = None,
        slug: str | None = None,
    ) -> WorkflowMeta:
        wid = workflow_id or generate_workflow_id()
        now = _now()
        record: dict[str, Any] = {
            "workflow_id": wid,
            "name": name,
            "description": description,
            "status": status.value,
            "current_version": 1,
            "owner_id": owner_id,
            "tags": list(tags or []),
            "category": category,
            "node_types": list(summary.node_types),
            "summary": summary.model_dump(mode="json"),
            "latest_doc": _stamp_doc_id(doc, wid),
            "created_at": now,
            "updated_at": now,
        }
        if slug is not None:
            record["slug"] = slug
        try:
            await self._coll.insert_one(record)
        except DuplicateKeyError as e:
            raise ConflictError(f"workflow_id or slug already exists: {e}") from e
        return _meta_from_record(record)

    # ---------- read ----------

    async def get_meta(self, workflow_id: str) -> WorkflowMeta | None:
        projection = {k: 1 for k in META_FIELDS}
        record = await self._coll.find_one({"workflow_id": workflow_id}, projection=projection)
        return None if record is None else _meta_from_record(record)

    async def get(self, workflow_id: str) -> WorkflowResponse | None:
        record = await self._coll.find_one({"workflow_id": workflow_id})
        if record is None:
            return None
        return WorkflowResponse(meta=_meta_from_record(record), doc=_doc_from_record(record))

    async def exists(self, workflow_id: str) -> bool:
        return (await self._coll.count_documents({"workflow_id": workflow_id}, limit=1)) > 0

    # ---------- update ----------

    async def update_head(
        self,
        *,
        workflow_id: str,
        doc: WorkflowDoc,
        summary: WorkflowSummary,
        new_version: int,
        expected_current_version: int,
    ) -> WorkflowMeta:
        """Atomic OCC update: set the new version + doc + summary IFF the
        head's ``current_version`` matches ``expected_current_version``.

        Raises:
            NotFoundError: workflow does not exist
            ConflictError: head moved since caller read it
        """
        record = await self._coll.find_one_and_update(
            {
                "workflow_id": workflow_id,
                "current_version": expected_current_version,
            },
            {
                "$set": {
                    "current_version": new_version,
                    "latest_doc": _stamp_doc_id(doc, workflow_id),
                    "summary": summary.model_dump(mode="json"),
                    "node_types": list(summary.node_types),
                    "updated_at": _now(),
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if record is not None:
            return _meta_from_record(record)
        existing = await self.get_meta(workflow_id)
        if existing is None:
            raise NotFoundError(f"workflow {workflow_id!r} not found")
        raise ConflictError(
            f"version mismatch: expected current_version={expected_current_version}, "
            f"actual={existing.current_version}"
        )

    async def patch_metadata(
        self,
        workflow_id: str,
        changes: dict[str, Any],
    ) -> WorkflowMeta:
        """Partial metadata update. Caller passes a dict of field -> value
        (typically ``PatchWorkflowRequest.model_dump(exclude_unset=True)``).

        Does not bump version. Empty ``changes`` is treated as a fetch.
        """
        if not changes:
            existing = await self.get_meta(workflow_id)
            if existing is None:
                raise NotFoundError(f"workflow {workflow_id!r} not found")
            return existing

        update: dict[str, Any] = {}
        allowed = {"name", "description", "tags", "category", "status"}
        for key, value in changes.items():
            if key not in allowed:
                continue
            if key == "status" and isinstance(value, WorkflowStatus):
                update[key] = value.value
            else:
                update[key] = value
        update["updated_at"] = _now()

        record = await self._coll.find_one_and_update(
            {"workflow_id": workflow_id},
            {"$set": update},
            return_document=ReturnDocument.AFTER,
        )
        if record is None:
            raise NotFoundError(f"workflow {workflow_id!r} not found")
        return _meta_from_record(record)

    # ---------- archive / restore / delete ----------

    async def archive(self, workflow_id: str) -> WorkflowMeta:
        return await self.patch_metadata(workflow_id, {"status": WorkflowStatus.ARCHIVED})

    async def unarchive(
        self,
        workflow_id: str,
        *,
        target_status: WorkflowStatus = WorkflowStatus.DRAFT,
    ) -> WorkflowMeta:
        return await self.patch_metadata(workflow_id, {"status": target_status})

    async def soft_delete(self, workflow_id: str) -> WorkflowMeta:
        """Alias for ``archive``: marks the workflow archived without removing it."""
        return await self.archive(workflow_id)

    async def restore(
        self,
        workflow_id: str,
        *,
        target_status: WorkflowStatus = WorkflowStatus.DRAFT,
    ) -> WorkflowMeta:
        """Alias for ``unarchive``."""
        return await self.unarchive(workflow_id, target_status=target_status)

    async def hard_delete(self, workflow_id: str) -> None:
        """Permanently removes the head document.

        Does NOT cascade to ``workflow_versions`` or ``workflow_audit_logs``;
        the orchestrating service is responsible for calling the cascade
        helpers on those repos.
        """
        result = await self._coll.delete_one({"workflow_id": workflow_id})
        if result.deleted_count == 0:
            raise NotFoundError(f"workflow {workflow_id!r} not found")

    # ---------- duplicate ----------

    async def duplicate(
        self,
        source_workflow_id: str,
        *,
        new_name: str | None = None,
        new_workflow_id: str | None = None,
        owner_id: str | None = None,
    ) -> WorkflowMeta:
        """Copy a workflow's latest doc + metadata under a fresh id. The new
        workflow starts at version 1 and status=draft."""
        src = await self._coll.find_one({"workflow_id": source_workflow_id})
        if src is None:
            raise NotFoundError(f"workflow {source_workflow_id!r} not found")

        new_id = new_workflow_id or generate_workflow_id()
        name = new_name or f"{src['name']} (copy)"
        now = _now()
        latest_doc = dict(src.get("latest_doc") or {})
        latest_doc["id"] = new_id
        record = {
            "workflow_id": new_id,
            "name": name,
            "description": src.get("description"),
            "status": WorkflowStatus.DRAFT.value,
            "current_version": 1,
            "owner_id": owner_id if owner_id is not None else src.get("owner_id"),
            "tags": list(src.get("tags") or []),
            "category": src.get("category"),
            "node_types": list(src.get("node_types") or []),
            "summary": src.get("summary"),
            "latest_doc": latest_doc,
            "created_at": now,
            "updated_at": now,
        }
        try:
            await self._coll.insert_one(record)
        except DuplicateKeyError as e:
            raise ConflictError(f"workflow_id {new_id!r} already exists") from e
        return _meta_from_record(record)

    # ---------- list / search ----------

    SORTABLE_FIELDS: frozenset[str] = frozenset(
        {"updated_at", "created_at", "name", "current_version"}
    )

    async def list(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        owner_id: str | None = None,
        status: WorkflowStatus | str | None = None,
        tag: str | None = None,
        category: str | None = None,
        node_type: str | None = None,
        name_contains: str | None = None,
        validation_status: str | None = None,
        include_archived: bool = False,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> tuple[list[WorkflowMeta], int]:
        """Paginated, filterable list. Returns ``(items, total_count)``.

        ``sort_by`` is silently coerced to ``updated_at`` if not in
        ``SORTABLE_FIELDS``. ``sort_order`` is ``"asc"`` or ``"desc"``.
        """
        query: dict[str, Any] = {}
        if owner_id is not None:
            query["owner_id"] = owner_id
        if status is not None:
            query["status"] = status.value if isinstance(status, WorkflowStatus) else status
        elif not include_archived:
            query["status"] = {"$ne": WorkflowStatus.ARCHIVED.value}
        if tag is not None:
            query["tags"] = tag
        if category is not None:
            query["category"] = category
        if node_type is not None:
            query["node_types"] = node_type
        if name_contains:
            query["name"] = {"$regex": re.escape(name_contains), "$options": "i"}
        if validation_status is not None:
            query["summary.validation_status"] = validation_status

        sort_field = sort_by if sort_by in self.SORTABLE_FIELDS else "updated_at"
        direction = -1 if sort_order == "desc" else 1
        # Stable tie-break on _id (monotonic per-process).
        sort_spec: list[tuple[str, int]] = [(sort_field, direction), ("_id", -1)]

        total = await self._coll.count_documents(query)
        projection = {k: 1 for k in META_FIELDS}
        cursor = (
            self._coll.find(query, projection=projection)
            .sort(sort_spec)
            .skip(offset)
            .limit(limit)
        )
        items: list[WorkflowMeta] = []
        async for record in cursor:
            items.append(_meta_from_record(record))
        return items, total

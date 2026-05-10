"""Index specifications and creation service.

Index design:

- ``workflows`` is the head/index collection. The set of single-field indexes
  matches the list/filter facets the React Workflow Builder will expose:
  filter by owner/status/category/tag, search by node type, sort by recency.
- ``slug`` is unique but sparse: documents without a slug do not collide.
- ``workflow_versions`` carries a single compound unique index that pins the
  monotonic per-workflow version sequence.
- ``workflow_audit_logs`` is read in reverse chronological order per workflow,
  so the index is ``(workflow_id ASC, created_at DESC)``.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pymongo import ASCENDING, DESCENDING, IndexModel

from app.db import collections as col

if TYPE_CHECKING:
    from pymongo.asynchronous.database import AsyncDatabase

log = logging.getLogger("app.db.indexes")


WORKFLOW_INDEXES: list[IndexModel] = [
    IndexModel([("workflow_id", ASCENDING)], name="workflow_id_unique", unique=True),
    IndexModel([("slug", ASCENDING)], name="slug_unique", unique=True, sparse=True),
    IndexModel([("owner_id", ASCENDING)], name="owner_id"),
    IndexModel([("status", ASCENDING)], name="status"),
    IndexModel([("tags", ASCENDING)], name="tags"),
    IndexModel([("category", ASCENDING)], name="category"),
    IndexModel([("updated_at", DESCENDING)], name="updated_at_desc"),
    IndexModel([("created_at", DESCENDING)], name="created_at_desc"),
    IndexModel([("node_types", ASCENDING)], name="node_types"),
    IndexModel([("current_version", ASCENDING)], name="current_version"),
]

WORKFLOW_VERSIONS_INDEXES: list[IndexModel] = [
    IndexModel(
        [("workflow_id", ASCENDING), ("version", ASCENDING)],
        name="workflow_id_version_unique",
        unique=True,
    ),
]

WORKFLOW_AUDIT_LOGS_INDEXES: list[IndexModel] = [
    IndexModel(
        [("workflow_id", ASCENDING), ("created_at", DESCENDING)],
        name="workflow_id_created_at_desc",
    ),
]


INDEX_PLAN: dict[str, list[IndexModel]] = {
    col.WORKFLOWS: WORKFLOW_INDEXES,
    col.WORKFLOW_VERSIONS: WORKFLOW_VERSIONS_INDEXES,
    col.WORKFLOW_AUDIT_LOGS: WORKFLOW_AUDIT_LOGS_INDEXES,
}


async def ensure_indexes(db: AsyncDatabase) -> dict[str, list[str]]:
    """Create or update every index in INDEX_PLAN. Idempotent."""
    created: dict[str, list[str]] = {}
    for collection_name, models in INDEX_PLAN.items():
        names = await db[collection_name].create_indexes(models)
        created[collection_name] = names
        log.info("indexes ensured collection=%s names=%s", collection_name, names)
    return created

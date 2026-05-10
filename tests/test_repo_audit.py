"""Integration tests for ``WorkflowAuditLogRepository``."""

from __future__ import annotations

import os
import secrets

import pytest

from app.db.indexes import ensure_indexes
from app.db.mongo import MongoManager
from app.db.repos.audit_log_repo import WorkflowAuditLogRepository
from app.models.audit import AuditAction

_URI = os.environ.get("TEST_MONGODB_URI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_URI is None, reason="set TEST_MONGODB_URI to run"),
]


@pytest.fixture
async def db():
    name = f"abm_repo_a_{secrets.token_hex(4)}"
    mgr = MongoManager(uri=_URI, db_name=name)
    try:
        await ensure_indexes(mgr.db)
        yield mgr.db
    finally:
        await mgr.client.drop_database(name)
        await mgr.close()


# --------------------------- write ---------------------------


async def test_write_basic(db):
    repo = WorkflowAuditLogRepository(db)
    entry = await repo.write(
        workflow_id="wf_a",
        action=AuditAction.CREATED,
        actor_id="alice",
        version=1,
        details={"name": "Demo"},
    )
    assert entry.workflow_id == "wf_a"
    assert entry.action is AuditAction.CREATED
    assert entry.actor_id == "alice"
    assert entry.version == 1
    assert entry.details == {"name": "Demo"}


async def test_write_accepts_action_string(db):
    repo = WorkflowAuditLogRepository(db)
    entry = await repo.write(workflow_id="wf_a", action="updated")
    assert entry.action is AuditAction.UPDATED


async def test_write_with_minimal_fields(db):
    repo = WorkflowAuditLogRepository(db)
    entry = await repo.write(workflow_id="wf_a", action=AuditAction.PATCHED)
    assert entry.actor_id is None
    assert entry.version is None
    assert entry.details == {}


# --------------------------- list ---------------------------


async def test_list_returns_newest_first(db):
    repo = WorkflowAuditLogRepository(db)
    # Sequential awaits → strictly increasing created_at
    for action in (AuditAction.CREATED, AuditAction.UPDATED, AuditAction.PATCHED):
        await repo.write(workflow_id="wf_a", action=action)
    items, total = await repo.list("wf_a")
    assert total == 3
    assert [e.action for e in items] == [
        AuditAction.PATCHED,
        AuditAction.UPDATED,
        AuditAction.CREATED,
    ]


async def test_list_pagination(db):
    repo = WorkflowAuditLogRepository(db)
    for _ in range(5):
        await repo.write(workflow_id="wf_a", action=AuditAction.UPDATED)
    page1, total = await repo.list("wf_a", limit=2, offset=0)
    page2, _ = await repo.list("wf_a", limit=2, offset=2)
    assert total == 5
    assert len(page1) == 2 and len(page2) == 2


async def test_list_scoped_to_workflow(db):
    repo = WorkflowAuditLogRepository(db)
    await repo.write(workflow_id="wf_a", action=AuditAction.CREATED)
    await repo.write(workflow_id="wf_b", action=AuditAction.CREATED)
    items_a, _ = await repo.list("wf_a")
    items_b, _ = await repo.list("wf_b")
    assert len(items_a) == 1 and len(items_b) == 1
    assert items_a[0].workflow_id == "wf_a"


# --------------------------- cascade ---------------------------


async def test_delete_all_for_workflow(db):
    repo = WorkflowAuditLogRepository(db)
    for _ in range(3):
        await repo.write(workflow_id="wf_a", action=AuditAction.UPDATED)
    await repo.write(workflow_id="wf_b", action=AuditAction.UPDATED)
    deleted = await repo.delete_all_for_workflow("wf_a")
    assert deleted == 3
    _, remaining_a = await repo.list("wf_a")
    _, remaining_b = await repo.list("wf_b")
    assert remaining_a == 0
    assert remaining_b == 1

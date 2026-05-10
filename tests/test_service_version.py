"""Integration tests for ``WorkflowVersionService``."""

from __future__ import annotations

import os
import secrets

import pytest

from app.db.indexes import ensure_indexes
from app.db.mongo import MongoManager
from app.db.repos.audit_log_repo import WorkflowAuditLogRepository
from app.db.repos.version_repo import WorkflowVersionRepository
from app.db.repos.workflow_repo import WorkflowRepository
from app.errors import NotFoundError
from app.models.audit import AuditAction
from app.models.requests import CreateWorkflowRequest, UpdateWorkflowRequest
from app.models.workflow import WorkflowDoc
from app.services.version_service import WorkflowVersionService
from app.services.workflow_service import WorkflowService

_URI = os.environ.get("TEST_MONGODB_URI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_URI is None, reason="set TEST_MONGODB_URI to run"),
]


@pytest.fixture
async def db():
    name = f"abm_svc_v_{secrets.token_hex(4)}"
    mgr = MongoManager(uri=_URI, db_name=name)
    try:
        await ensure_indexes(mgr.db)
        yield mgr.db
    finally:
        await mgr.client.drop_database(name)
        await mgr.close()


@pytest.fixture
def services(db):
    workflow_repo = WorkflowRepository(db)
    version_repo = WorkflowVersionRepository(db)
    audit_repo = WorkflowAuditLogRepository(db)
    workflow_service = WorkflowService(
        workflow_repo=workflow_repo,
        version_repo=version_repo,
        audit_repo=audit_repo,
    )
    version_service = WorkflowVersionService(
        version_repo=version_repo,
        workflow_service=workflow_service,
    )
    return workflow_service, version_service, audit_repo


def _valid_doc(workflow_id: str = "wf_x", *, name: str = "X") -> WorkflowDoc:
    return WorkflowDoc.model_validate(
        {
            "id": workflow_id,
            "name": name,
            "nodes": [
                {"id": "s", "type": "start", "position": {"x": 0, "y": 0},
                 "data": {"name": "S", "config": {}}},
                {"id": "o", "type": "output", "position": {"x": 100, "y": 0},
                 "data": {"name": "O", "config": {}}},
            ],
            "edges": [{"id": "e1", "source": "s", "target": "o",
                       "sourceHandle": None, "targetHandle": None}],
        }
    )


# --------------------------- list ---------------------------


async def test_list_versions_newest_first(services):
    workflow_service, version_service, _ = services
    create = await workflow_service.create_workflow(
        CreateWorkflowRequest(name="A", doc=_valid_doc())
    )
    wid = create.meta.workflow_id
    await workflow_service.update_workflow(
        wid, UpdateWorkflowRequest(doc=_valid_doc(wid, name="A v2"))
    )
    await workflow_service.update_workflow(
        wid, UpdateWorkflowRequest(doc=_valid_doc(wid, name="A v3"))
    )
    response = await version_service.list_versions(wid)
    assert response.pagination.total == 3
    assert [v.version for v in response.items] == [3, 2, 1]


async def test_list_versions_pagination(services):
    workflow_service, version_service, _ = services
    create = await workflow_service.create_workflow(
        CreateWorkflowRequest(name="A", doc=_valid_doc())
    )
    wid = create.meta.workflow_id
    for _ in range(4):
        await workflow_service.update_workflow(
            wid, UpdateWorkflowRequest(doc=_valid_doc(wid))
        )
    page1 = await version_service.list_versions(wid, page=1, page_size=2)
    page2 = await version_service.list_versions(wid, page=2, page_size=2)
    assert page1.pagination.total == 5
    assert [v.version for v in page1.items] == [5, 4]
    assert [v.version for v in page2.items] == [3, 2]


async def test_list_versions_empty_for_unknown_workflow(services):
    _, version_service, _ = services
    response = await version_service.list_versions("wf_missing")
    assert response.pagination.total == 0
    assert response.items == []


# --------------------------- get ---------------------------


async def test_get_version_returns_doc(services):
    workflow_service, version_service, _ = services
    create = await workflow_service.create_workflow(
        CreateWorkflowRequest(name="A", doc=_valid_doc())
    )
    wid = create.meta.workflow_id
    response = await version_service.get_version(wid, 1)
    assert response.version == 1
    assert response.doc.id == wid


async def test_get_version_missing_raises_not_found(services):
    _, version_service, _ = services
    with pytest.raises(NotFoundError):
        await version_service.get_version("wf_missing", 1)


# --------------------------- rollback ---------------------------


async def test_rollback_writes_new_version_with_old_doc(services):
    workflow_service, version_service, audit_repo = services
    create = await workflow_service.create_workflow(
        CreateWorkflowRequest(name="A", doc=_valid_doc(name="v1 doc"))
    )
    wid = create.meta.workflow_id
    await workflow_service.update_workflow(
        wid, UpdateWorkflowRequest(doc=_valid_doc(wid, name="v2 doc"))
    )
    await workflow_service.update_workflow(
        wid, UpdateWorkflowRequest(doc=_valid_doc(wid, name="v3 doc"))
    )
    rolled = await version_service.rollback_to(wid, target_version=1, actor_id="alice")
    assert rolled.meta.current_version == 4
    assert rolled.doc.name == "v1 doc"

    # Audit should record ROLLED_BACK with target_version detail
    audits, _ = await audit_repo.list(wid)
    rb = [a for a in audits if a.action is AuditAction.ROLLED_BACK]
    assert len(rb) == 1
    assert rb[0].details.get("target_version") == 1


async def test_rollback_to_missing_version_raises(services):
    workflow_service, version_service, _ = services
    create = await workflow_service.create_workflow(
        CreateWorkflowRequest(name="A", doc=_valid_doc())
    )
    with pytest.raises(NotFoundError):
        await version_service.rollback_to(create.meta.workflow_id, target_version=99)


async def test_rollback_uses_default_change_note(services):
    workflow_service, version_service, _ = services
    create = await workflow_service.create_workflow(
        CreateWorkflowRequest(name="A", doc=_valid_doc())
    )
    wid = create.meta.workflow_id
    await workflow_service.update_workflow(
        wid, UpdateWorkflowRequest(doc=_valid_doc(wid, name="v2"))
    )
    await version_service.rollback_to(wid, target_version=1)
    # The new head version row should have the default change note.
    response = await version_service.get_version(wid, 3)
    assert response.change_note == "Rolled back to v1"

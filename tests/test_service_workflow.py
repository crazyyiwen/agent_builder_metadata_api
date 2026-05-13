"""Integration tests for ``WorkflowService``."""

from __future__ import annotations

import os
import secrets

import pytest

from app.db.indexes import ensure_indexes
from app.db.mongo import MongoManager
from app.db.repos.audit_log_repo import WorkflowAuditLogRepository
from app.db.repos.version_repo import WorkflowVersionRepository
from app.db.repos.workflow_repo import WorkflowRepository
from app.errors import (
    ConflictError,
    NotFoundError,
    OperationNotAllowedError,
    ValidationError,
)
from app.models.audit import AuditAction
from app.models.meta import WorkflowStatus
from app.models.requests import (
    CreateWorkflowRequest,
    PatchWorkflowRequest,
    UpdateWorkflowRequest,
)
from app.models.workflow import WorkflowDoc
from app.services.workflow_service import WorkflowService

_URI = os.environ.get("TEST_MONGODB_URI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_URI is None, reason="set TEST_MONGODB_URI to run"),
]


# --------------------------- fixtures ---------------------------


@pytest.fixture
async def db():
    name = f"abm_svc_wf_{secrets.token_hex(4)}"
    mgr = MongoManager(uri=_URI, db_name=name)
    try:
        await ensure_indexes(mgr.db)
        yield mgr.db
    finally:
        await mgr.client.drop_database(name)
        await mgr.close()


@pytest.fixture
def repos(db):
    return (
        WorkflowRepository(db),
        WorkflowVersionRepository(db),
        WorkflowAuditLogRepository(db),
    )


@pytest.fixture
def service(repos):
    workflow_repo, version_repo, audit_repo = repos
    return WorkflowService(
        workflow_repo=workflow_repo,
        version_repo=version_repo,
        audit_repo=audit_repo,
        allow_hard_delete=True,
    )


def _valid_doc(workflow_id: str = "wf_x", name: str = "X") -> WorkflowDoc:
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


def _invalid_doc(workflow_id: str = "wf_x", name: str = "X") -> WorkflowDoc:
    """Edge target references a non-existent node — fails default validation
    with a hard error (not a warning) so create/update reject it."""
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
            "edges": [
                {"id": "e1", "source": "s", "target": "o",
                 "sourceHandle": None, "targetHandle": None},
                {"id": "e2", "source": "s", "target": "ghost_node",
                 "sourceHandle": None, "targetHandle": None},
            ],
        }
    )


# --------------------------- create ---------------------------


async def test_create_with_doc_writes_head_version_and_audit(service, repos):
    workflow_repo, version_repo, audit_repo = repos
    request = CreateWorkflowRequest(name="Demo", doc=_valid_doc(), tags=["t1"])
    response = await service.create_workflow(request, actor_id="alice")

    assert response.meta.current_version == 1
    assert response.meta.name == "Demo"
    assert response.meta.tags == ["t1"]
    assert response.doc.id == response.meta.workflow_id

    # Version row written
    v1 = await version_repo.get(response.meta.workflow_id, 1)
    assert v1 is not None
    assert v1.doc.id == response.meta.workflow_id
    assert v1.created_by == "alice"

    # Audit row written
    audits, _ = await audit_repo.list(response.meta.workflow_id)
    assert any(a.action is AuditAction.CREATED for a in audits)


async def test_create_without_doc_uses_default_skeleton(service):
    request = CreateWorkflowRequest(name="Empty")
    response = await service.create_workflow(request)
    assert {n.type for n in response.doc.nodes} == {"start", "output"}
    assert len(response.doc.edges) == 1


async def test_create_with_invalid_doc_raises_validation_error(service):
    request = CreateWorkflowRequest(name="Bad", doc=_invalid_doc())
    with pytest.raises(ValidationError) as exc:
        await service.create_workflow(request)
    assert any(i.code == "EDGE_TARGET_UNKNOWN" for i in exc.value.issues)


async def test_create_stamps_workflow_id_into_doc_id(service):
    """Caller may pass an arbitrary doc.id; server overrides with the new wf id."""
    request = CreateWorkflowRequest(name="X", doc=_valid_doc("wf_caller_chose"))
    response = await service.create_workflow(request)
    assert response.doc.id == response.meta.workflow_id
    assert response.doc.id != "wf_caller_chose"


# --------------------------- update ---------------------------


async def test_update_bumps_version_and_writes_new_version_row(service, repos):
    _, version_repo, _ = repos
    create = await service.create_workflow(CreateWorkflowRequest(name="A", doc=_valid_doc()))
    wid = create.meta.workflow_id

    new_doc = _valid_doc(wid, name="A v2")
    response = await service.update_workflow(
        wid,
        UpdateWorkflowRequest(doc=new_doc, change_note="renamed"),
        actor_id="bob",
    )
    assert response.meta.current_version == 2

    v2 = await version_repo.get(wid, 2)
    assert v2 is not None
    assert v2.change_note == "renamed"
    assert v2.created_by == "bob"


async def test_update_with_stale_version_raises_conflict(service):
    create = await service.create_workflow(CreateWorkflowRequest(name="A", doc=_valid_doc()))
    wid = create.meta.workflow_id
    await service.update_workflow(
        wid, UpdateWorkflowRequest(doc=_valid_doc(wid)), expected_version=1,
    )
    with pytest.raises(ConflictError):
        await service.update_workflow(
            wid, UpdateWorkflowRequest(doc=_valid_doc(wid)), expected_version=1,  # stale
        )


async def test_update_with_invalid_doc_does_not_change_head(service):
    create = await service.create_workflow(CreateWorkflowRequest(name="A", doc=_valid_doc()))
    wid = create.meta.workflow_id
    with pytest.raises(ValidationError):
        await service.update_workflow(
            wid, UpdateWorkflowRequest(doc=_invalid_doc(wid)),
        )
    after = await service.get_workflow(wid)
    assert after.meta.current_version == 1


async def test_update_missing_workflow_raises_not_found(service):
    with pytest.raises(NotFoundError):
        await service.update_workflow(
            "wf_missing", UpdateWorkflowRequest(doc=_valid_doc("wf_missing")),
        )


# --------------------------- patch ---------------------------


async def test_patch_changes_metadata_without_bumping_version(service, repos):
    _, _, audit_repo = repos
    create = await service.create_workflow(CreateWorkflowRequest(name="Before", doc=_valid_doc()))
    wid = create.meta.workflow_id
    meta = await service.patch_workflow(
        wid,
        PatchWorkflowRequest(name="After", tags=["new"]),
        actor_id="alice",
    )
    assert meta.name == "After"
    assert meta.tags == ["new"]
    assert meta.current_version == 1

    audits, _ = await audit_repo.list(wid)
    patched = [a for a in audits if a.action is AuditAction.PATCHED]
    assert len(patched) == 1
    assert patched[0].details["changed_fields"] == ["name", "tags"]


# --------------------------- archive / restore / soft delete ---------------------------


async def test_archive_then_unarchive(service, repos):
    _, _, audit_repo = repos
    create = await service.create_workflow(CreateWorkflowRequest(name="A", doc=_valid_doc()))
    wid = create.meta.workflow_id
    archived = await service.archive_workflow(wid, actor_id="alice")
    assert archived.status is WorkflowStatus.ARCHIVED
    restored = await service.unarchive_workflow(wid, actor_id="alice")
    assert restored.status is WorkflowStatus.DRAFT

    actions = [a.action for a in (await audit_repo.list(wid))[0]]
    assert AuditAction.ARCHIVED in actions
    assert AuditAction.RESTORED in actions


async def test_soft_delete_and_restore_aliases(service):
    create = await service.create_workflow(CreateWorkflowRequest(name="A", doc=_valid_doc()))
    wid = create.meta.workflow_id
    soft = await service.soft_delete_workflow(wid)
    assert soft.status is WorkflowStatus.ARCHIVED
    restored = await service.restore_workflow(wid)
    assert restored.status is WorkflowStatus.DRAFT


# --------------------------- hard delete ---------------------------


async def test_hard_delete_cascades(service, repos):
    workflow_repo, version_repo, audit_repo = repos
    create = await service.create_workflow(CreateWorkflowRequest(name="A", doc=_valid_doc()))
    wid = create.meta.workflow_id
    await service.update_workflow(wid, UpdateWorkflowRequest(doc=_valid_doc(wid)))
    # Sanity: data exists in all three collections
    assert await workflow_repo.exists(wid)
    assert (await version_repo.list_summaries(wid))[1] >= 2
    assert (await audit_repo.list(wid))[1] >= 2

    await service.hard_delete_workflow(wid, actor_id="alice")

    assert not await workflow_repo.exists(wid)
    assert (await version_repo.list_summaries(wid))[1] == 0
    assert (await audit_repo.list(wid))[1] == 0


async def test_hard_delete_disabled_by_default():
    # Build a service WITHOUT allow_hard_delete=True.
    name = f"abm_svc_wf_{secrets.token_hex(4)}"
    mgr = MongoManager(uri=_URI, db_name=name)
    try:
        await ensure_indexes(mgr.db)
        wr = WorkflowRepository(mgr.db)
        vr = WorkflowVersionRepository(mgr.db)
        ar = WorkflowAuditLogRepository(mgr.db)
        svc = WorkflowService(workflow_repo=wr, version_repo=vr, audit_repo=ar)  # default False
        create = await svc.create_workflow(CreateWorkflowRequest(name="A", doc=_valid_doc()))
        with pytest.raises(OperationNotAllowedError):
            await svc.hard_delete_workflow(create.meta.workflow_id)
    finally:
        await mgr.client.drop_database(name)
        await mgr.close()


async def test_hard_delete_missing_workflow_raises_not_found(service):
    with pytest.raises(NotFoundError):
        await service.hard_delete_workflow("wf_missing")


# --------------------------- duplicate ---------------------------


async def test_duplicate_creates_new_workflow_with_v1_and_audit(service, repos):
    _, version_repo, audit_repo = repos
    src = await service.create_workflow(CreateWorkflowRequest(name="Original", doc=_valid_doc()))
    src_id = src.meta.workflow_id

    copy = await service.duplicate_workflow(src_id, actor_id="alice")
    assert copy.meta.workflow_id != src_id
    assert copy.meta.current_version == 1
    assert copy.doc.id == copy.meta.workflow_id

    v1 = await version_repo.get(copy.meta.workflow_id, 1)
    assert v1 is not None
    assert "Duplicated from" in (v1.change_note or "")

    audits, _ = await audit_repo.list(copy.meta.workflow_id)
    assert any(a.action is AuditAction.DUPLICATED for a in audits)


# --------------------------- list ---------------------------


async def test_list_workflows_paginates_and_reports_has_more(service):
    for i in range(5):
        await service.create_workflow(CreateWorkflowRequest(name=f"W{i}", doc=_valid_doc()))
    page1 = await service.list_workflows(page=1, page_size=2)
    page2 = await service.list_workflows(page=2, page_size=2)
    page3 = await service.list_workflows(page=3, page_size=2)
    assert page1.pagination.total == 5
    assert page1.pagination.has_more is True
    assert page2.pagination.has_more is True
    assert page3.pagination.has_more is False
    assert len(page3.items) == 1


# --------------------------- stateless validate ---------------------------


async def test_validate_doc_returns_report_without_persisting(service, repos):
    workflow_repo, _, _ = repos
    report = service.validate_doc(_valid_doc())
    assert report.valid is True
    # Nothing was written
    items, total = await workflow_repo.list()
    assert total == 0
    assert items == []

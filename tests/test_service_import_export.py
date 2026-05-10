"""Integration tests for ``ImportExportService``."""

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
from app.models.requests import CreateWorkflowRequest
from app.services.import_export_service import ImportExportService
from app.services.workflow_service import WorkflowService

_URI = os.environ.get("TEST_MONGODB_URI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_URI is None, reason="set TEST_MONGODB_URI to run"),
]


@pytest.fixture
async def db():
    name = f"abm_svc_ie_{secrets.token_hex(4)}"
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
    ie_service = ImportExportService(
        workflow_service=workflow_service,
        version_repo=version_repo,
    )
    return workflow_service, ie_service


def _bare_doc_payload(name: str = "Imported", *, future_field: bool = False) -> dict:
    """A bare WorkflowDoc-shaped JSON payload."""
    payload = {
        "id": "wf_caller_chose",
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
    if future_field:
        # NodeData has extra="allow"; this future field should round-trip.
        payload["nodes"][1]["data"]["futureField"] = "preserved"
    return payload


# --------------------------- import (create) ---------------------------


async def test_import_bare_doc_creates_new_workflow(services):
    _, ie = services
    response = await ie.import_workflow(_bare_doc_payload(), actor_id="alice")
    assert response.created is True
    assert response.version == 1
    assert response.workflow_id.startswith("wf_")
    assert response.validation.valid is True


async def test_import_envelope_with_metadata_creates_workflow(services):
    workflow_service, ie = services
    payload = {
        "name": "From Envelope",
        "description": "imported test",
        "tags": ["a", "b"],
        "category": "ops",
        "doc": _bare_doc_payload(name="Inner Name"),
    }
    response = await ie.import_workflow(payload)
    assert response.created is True
    fetched = await workflow_service.get_workflow(response.workflow_id)
    assert fetched.meta.name == "From Envelope"
    assert fetched.meta.tags == ["a", "b"]
    assert fetched.meta.category == "ops"
    assert fetched.meta.description == "imported test"


async def test_import_preserves_unknown_node_data_fields(services):
    workflow_service, ie = services
    response = await ie.import_workflow(_bare_doc_payload(future_field=True))
    fetched = await workflow_service.get_workflow(response.workflow_id)
    output_node = next(n for n in fetched.doc.nodes if n.type == "output")
    dumped = output_node.data.model_dump()
    assert dumped.get("futureField") == "preserved"


# --------------------------- import (update existing) ---------------------------


async def test_import_with_replace_workflow_id_updates_existing(services):
    workflow_service, ie = services
    create = await workflow_service.create_workflow(
        CreateWorkflowRequest(name="Original", doc=None)
    )
    wid = create.meta.workflow_id
    response = await ie.import_workflow(
        _bare_doc_payload(name="Updated"), replace_workflow_id=wid,
    )
    assert response.created is False
    assert response.workflow_id == wid
    assert response.version == 2

    fetched = await workflow_service.get_workflow(wid)
    assert fetched.meta.current_version == 2


async def test_import_replace_missing_workflow_raises(services):
    _, ie = services
    with pytest.raises(NotFoundError):
        await ie.import_workflow(
            _bare_doc_payload(), replace_workflow_id="wf_missing",
        )


# --------------------------- export ---------------------------


async def test_export_latest_returns_current_doc(services):
    workflow_service, ie = services
    create = await workflow_service.create_workflow(
        CreateWorkflowRequest(name="Demo", doc=None)
    )
    wid = create.meta.workflow_id
    export = await ie.export_latest(wid)
    assert export.workflow_id == wid
    assert export.version == 1
    assert export.doc.id == wid


async def test_export_latest_missing_raises(services):
    _, ie = services
    with pytest.raises(NotFoundError):
        await ie.export_latest("wf_missing")


async def test_export_specific_version(services):
    from app.models.requests import UpdateWorkflowRequest
    from app.models.workflow import WorkflowDoc

    workflow_service, ie = services
    create = await workflow_service.create_workflow(
        CreateWorkflowRequest(name="A", doc=WorkflowDoc.model_validate(_bare_doc_payload(name="v1")))
    )
    wid = create.meta.workflow_id
    await workflow_service.update_workflow(
        wid, UpdateWorkflowRequest(doc=WorkflowDoc.model_validate(_bare_doc_payload(name="v2"))),
    )
    export = await ie.export_version(wid, version=1)
    assert export.version == 1
    assert export.doc.name == "v1"


async def test_export_version_missing_raises(services):
    workflow_service, ie = services
    create = await workflow_service.create_workflow(
        CreateWorkflowRequest(name="A", doc=None)
    )
    with pytest.raises(NotFoundError):
        await ie.export_version(create.meta.workflow_id, version=99)

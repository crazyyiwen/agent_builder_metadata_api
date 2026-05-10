"""Integration tests for ``WorkflowVersionRepository``."""

from __future__ import annotations

import os
import secrets

import pytest

from app.db.indexes import ensure_indexes
from app.db.mongo import MongoManager
from app.db.repos.version_repo import WorkflowVersionRepository
from app.errors import ConflictError
from app.models.common import ValidationResult
from app.models.workflow import WorkflowDoc
from app.services.validation_service import extract_summary, validate_workflow

_URI = os.environ.get("TEST_MONGODB_URI")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(_URI is None, reason="set TEST_MONGODB_URI to run"),
]


@pytest.fixture
async def db():
    name = f"abm_repo_v_{secrets.token_hex(4)}"
    mgr = MongoManager(uri=_URI, db_name=name)
    try:
        await ensure_indexes(mgr.db)
        yield mgr.db
    finally:
        await mgr.client.drop_database(name)
        await mgr.close()


def _doc(workflow_id: str = "wf_x", name: str = "X") -> WorkflowDoc:
    return WorkflowDoc.model_validate(
        {
            "id": workflow_id,
            "name": name,
            "nodes": [
                {
                    "id": "s",
                    "type": "start",
                    "position": {"x": 0, "y": 0},
                    "data": {"name": "S", "config": {}},
                },
                {
                    "id": "o",
                    "type": "output",
                    "position": {"x": 100, "y": 0},
                    "data": {"name": "O", "config": {}},
                },
            ],
            "edges": [
                {
                    "id": "e1",
                    "source": "s",
                    "target": "o",
                    "sourceHandle": None,
                    "targetHandle": None,
                }
            ],
        }
    )


# --------------------------- create ---------------------------


async def test_create_basic(db):
    repo = WorkflowVersionRepository(db)
    response = await repo.create(
        workflow_id="wf_a",
        version=1,
        doc=_doc("wf_a"),
        change_note="initial",
    )
    assert response.workflow_id == "wf_a"
    assert response.version == 1
    assert response.change_note == "initial"


async def test_create_duplicate_version_raises_conflict(db):
    repo = WorkflowVersionRepository(db)
    await repo.create(workflow_id="wf_a", version=1, doc=_doc("wf_a"))
    with pytest.raises(ConflictError):
        await repo.create(workflow_id="wf_a", version=1, doc=_doc("wf_a"))


async def test_create_persists_validation_and_summary(db):
    repo = WorkflowVersionRepository(db)
    doc = _doc("wf_a")
    validation = validate_workflow(doc)
    summary = extract_summary(doc, validation)
    await repo.create(
        workflow_id="wf_a",
        version=1,
        doc=doc,
        validation=validation,
        summary=summary,
    )
    fetched = await repo.get("wf_a", 1)
    assert fetched is not None
    assert isinstance(fetched.validation, ValidationResult)
    assert fetched.validation.valid is True


async def test_create_stamps_doc_id_to_match_workflow_id(db):
    repo = WorkflowVersionRepository(db)
    response = await repo.create(
        workflow_id="wf_target",
        version=1,
        doc=_doc("wf_other"),
    )
    fetched = await repo.get("wf_target", 1)
    assert fetched.doc.id == "wf_target"


# --------------------------- get / list / max ---------------------------


async def test_get_returns_none_when_missing(db):
    repo = WorkflowVersionRepository(db)
    assert await repo.get("wf_missing", 1) is None


async def test_list_summaries_newest_first(db):
    repo = WorkflowVersionRepository(db)
    for v in (1, 2, 3):
        await repo.create(workflow_id="wf_a", version=v, doc=_doc("wf_a"))
    items, total = await repo.list_summaries("wf_a")
    assert total == 3
    assert [i.version for i in items] == [3, 2, 1]
    # Doc field must be absent from the summary type.
    assert "doc" not in type(items[0]).model_fields


async def test_list_summaries_pagination(db):
    repo = WorkflowVersionRepository(db)
    for v in range(1, 6):
        await repo.create(workflow_id="wf_a", version=v, doc=_doc("wf_a"))
    page1, total = await repo.list_summaries("wf_a", limit=2, offset=0)
    page2, _ = await repo.list_summaries("wf_a", limit=2, offset=2)
    assert total == 5
    assert [i.version for i in page1] == [5, 4]
    assert [i.version for i in page2] == [3, 2]


async def test_get_max_version(db):
    repo = WorkflowVersionRepository(db)
    assert await repo.get_max_version("wf_empty") is None
    for v in (1, 2, 3, 7, 5):
        await repo.create(workflow_id="wf_a", version=v, doc=_doc("wf_a"))
    assert await repo.get_max_version("wf_a") == 7


async def test_get_doc_for_rollback(db):
    repo = WorkflowVersionRepository(db)
    await repo.create(
        workflow_id="wf_a",
        version=3,
        doc=_doc("wf_a", name="At v3"),
    )
    doc = await repo.get_doc_for_rollback("wf_a", 3)
    assert doc is not None
    assert doc.name == "At v3"


async def test_get_doc_for_rollback_returns_none_when_missing(db):
    repo = WorkflowVersionRepository(db)
    assert await repo.get_doc_for_rollback("wf_a", 99) is None


# --------------------------- cascade ---------------------------


async def test_delete_all_for_workflow(db):
    repo = WorkflowVersionRepository(db)
    for v in (1, 2, 3):
        await repo.create(workflow_id="wf_a", version=v, doc=_doc("wf_a"))
    await repo.create(workflow_id="wf_b", version=1, doc=_doc("wf_b"))
    deleted = await repo.delete_all_for_workflow("wf_a")
    assert deleted == 3
    _, remaining_a = await repo.list_summaries("wf_a")
    _, remaining_b = await repo.list_summaries("wf_b")
    assert remaining_a == 0
    assert remaining_b == 1
